#!/usr/bin/env python3
#
# Modified from https://github.com/StuPeter/Sougou_dict_spider/blob/master/SougouSpider.py
#
# See the LICENSE file for more information.

"""
DictSpider: A spider for downloading Sougou dictionaries.

This module provides the DictSpider class, which can download and organize
dictionaries from Sougou input method websites, supporting parallel downloads,
exclusion lists, and category selection.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

import queue_thread_pool_executor

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from concurrent.futures import Future
    from types import TracebackType

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class DictSpider:
    """A spider for downloading Sougou dictionaries."""

    MIN_PAGE_CATEGORY: Final = 2

    def __init__(
        self,
        categories: Iterable[str] | None = None,
        save_path: Path | None = None,
        exclude_list: Iterable[str] | None = None,
        concurrent_downloads: int | None = None,
        max_retries: int | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize the DictSpider.

        Args:
            categories (Iterable[str] | None):
                Iterable of category indices to be downloaded.
            save_path (Path | None): Directory to save dictionaries.
            exclude_list (Iterable[str] | None):
                Iterable of dictionary indices to exclude.
            concurrent_downloads (int | None): Number of parallel downloads.
            max_retries (int | None): Maximum number of retries for
                HTTP requests.
            timeout (float | None): Timeout for HTTP requests in seconds.
            headers (dict[str, str] | None): HTTP headers to use for requests.

        """
        self.categories = categories
        self.save_path = save_path or Path("sougou_dict")
        self.exclude_list = (
            set(exclude_list)
            if exclude_list is not None
            else {"2775", "15946", "176476"}
        )
        self.max_retries = max(0, max_retries or 20)
        self.timeout = timeout or 60
        self.headers = (
            headers
            if headers is not None
            else {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:60.0) "
                    "Gecko/20100101 Firefox/60.0"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "*/*;q=0.8"
                ),
                "Accept-Language": (
                    "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,"
                    "en-US;q=0.3,en;q=0.2"
                ),
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )
        self.stats: dict[str, int] = {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
        }
        self._concurrent_downloads = max(
            1, concurrent_downloads or min(32, (os.cpu_count() or 1) * 5)
        )
        self._thread_local = threading.local()
        self._sessions: list[requests.Session] = []
        self._executor = queue_thread_pool_executor.QueueThreadPoolExecutor(
            self._concurrent_downloads
        )
        self._lock = threading.Lock()
        self._futures: list[Future] = []
        self._errors: list[tuple[str, Exception]] = []

    def __enter__(self) -> Self:
        """
        Enter the runtime context related to this object.

        Automatically starts the download process when entering the context.

        Returns:
            Self: The DictSpider instance itself.

        """
        self._executor.__enter__()
        self._download_dicts()
        return self

    def __exit__(
        self,
        typ: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        """
        Exit the runtime context and clean up resources.

        Args:
            typ (type[BaseException] | None): Exception type, if any.
            exc (BaseException | None): Exception instance, if any.
            tb (TracebackType | None): Traceback, if any.

        Returns:
            bool | None: The return value from the executor's __exit__ method.

        Raises:
            RuntimeError: If any concurrent tasks failed.

        """
        try:
            rv = self._executor.__exit__(typ, exc, tb)

            for future in self._futures:
                if (exception := future.exception()) is not None:
                    self._errors.append((str(future), exception))
        finally:
            with contextlib.suppress(Exception):
                self._report_stats()
            with contextlib.suppress(Exception):
                for s in self._sessions:
                    with contextlib.suppress(Exception):
                        s.close()

        if self._errors:
            msg = f"Application finished with {len(self._errors)} errors."
            raise RuntimeError(msg)

        return rv

    def _submit(
        self, fn: Callable[..., object], /, *args: object, **kwargs: object
    ) -> None:
        future = self._executor.submit(fn, *args, **kwargs)
        with self._lock:
            self._futures.append(future)

    def _get_session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.mount(
                "https://",
                HTTPAdapter(
                    pool_connections=self._concurrent_downloads,
                    pool_maxsize=self._concurrent_downloads,
                ),
            )
            with self._lock:
                self._sessions.append(session)
            self._thread_local.session = session
        return session

    def _get_html(self, url: str) -> requests.Response:
        last_exception: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                response = self._get_session().get(
                    url, headers=self.headers, timeout=self.timeout
                )
                response.raise_for_status()
                if response.content:
                    return response

                log.warning("Downloaded content of %s is empty", url)
            except requests.RequestException as exc:
                last_exception = exc
                log.warning("Request failed for %s: %s", url, exc)

        msg = f"Failed to fetch {url}."
        log.error(msg)
        if last_exception:
            raise last_exception
        raise requests.RequestException(msg)

    def _download(self, name: str, url: str, category_path: Path) -> None:
        file_path = category_path / name
        if file_path.is_file():
            log.warning("%s already exists, skipping...", file_path)
            with self._lock:
                self.stats["skipped"] += 1
            return

        try:
            response = self._get_html(url)
            file_path.write_bytes(response.content)
            with self._lock:
                self.stats["downloaded"] += 1
            log.info("%s downloaded successfully.", name)
        except Exception:
            with self._lock:
                self.stats["failed"] += 1
            log.exception("Failed to download %s", name)
            raise

    def _download_page(self, page_url: str, category_path: Path) -> None:
        response = self._get_html(page_url)
        for dict_td in BeautifulSoup(response.text, "html.parser").find_all(
            "div", class_="dict_detail_block"
        ):
            if (
                dict_td_id := (
                    dict_td_title := dict_td.find(
                        "div", class_="detail_title"
                    ).a
                )["href"].rpartition("/")[-1]
            ) not in self.exclude_list:
                self._submit(
                    self._download,
                    (
                        dict_td_title.string
                        .replace("/", "-")
                        .replace(",", "-")
                        .replace("|", "-")
                        .replace("\\", "-")
                        .replace("'", "-")
                        if dict_td_title.string
                        else ""
                    )
                    + "_"
                    + dict_td_id
                    + ".scel",
                    dict_td.find("div", class_="dict_dl_btn").a["href"],
                    category_path,
                )

    def _download_category(
        self,
        category: str,
        category_167: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        category_url = "https://pinyin.sogou.com/dict/cate/index/" + category
        response = self._get_html(category_url)
        soup = BeautifulSoup(response.text, "html.parser")
        if not category_167:
            category_path = self.save_path / (
                soup.find("title").string.partition("_")[0] + "_" + category
            )
            category_path.mkdir(parents=True, exist_ok=True)
        else:
            category_path = self.save_path / "城市信息大全_167"
        page_n = (
            DictSpider.MIN_PAGE_CATEGORY
            if (page_list := soup.find("div", id="dict_page_list")) is None
            or len(pages := page_list.find_all("a"))
            < DictSpider.MIN_PAGE_CATEGORY
            else int(pages[-2].string) + 1
        )
        for page in range(1, page_n):
            self._submit(
                self._download_page,
                category_url + "/default/" + str(page),
                category_path,
            )

    def _download_category_167(self) -> None:
        response = self._get_html(
            "https://pinyin.sogou.com/dict/cate/index/180"
        )
        category_path = self.save_path / "城市信息大全_167"
        category_path.mkdir(parents=True, exist_ok=True)
        soup = BeautifulSoup(response.text, "html.parser")
        for category_td in soup.find_all("div", class_="citylistcate"):
            self._submit(
                self._download_category,
                category_td.a["href"].rpartition("/")[-1],
                True,  # noqa: FBT003
            )

    def _download_category_0(self) -> None:
        response = self._get_html(
            "https://pinyin.sogou.com/dict/detail/index/4"
        )
        category_path = self.save_path / "未分类_0"
        category_path.mkdir(parents=True, exist_ok=True)
        self._submit(
            self._download,
            "网络流行新词【官方推荐】_4.scel",
            "https://pinyin.sogou.com/d/dict/download_cell.php?id=4&name=网络流行新词【官方推荐】",
            category_path,
        )
        for dict_td in BeautifulSoup(response.text, "html.parser").find_all(
            "div", class_="rcmd_dict"
        ):
            self._submit(
                self._download,
                (
                    dict_td_title := dict_td.find(
                        "div", class_="rcmd_dict_title"
                    ).a
                ).string
                + "_"
                + dict_td_title["href"].rpartition("/")[-1]
                + ".scel",
                "https:"
                + dict_td.find("div", class_="rcmd_dict_dl_btn").a["href"],
                category_path,
            )

    def _download_dicts(self) -> None:
        if self.categories is None:
            main_url = "https://pinyin.sogou.com/dict/"
            response = self._get_html(main_url)
            soup = BeautifulSoup(response.text, "html.parser")
            category_iter = (
                category.a["href"].partition("?")[0].rpartition("/")[-1]
                for category in soup.find_all(
                    "div", class_="dict_category_list_title"
                )
            )
            iterable = itertools.chain(["0"], category_iter)
        else:
            iterable = self.categories
        for category in iterable:
            if category == "0":
                self._submit(self._download_category_0)
            elif category == "167":
                self._submit(self._download_category_167)
            else:
                self._submit(self._download_category, category)

    def _report_stats(self) -> None:
        downloaded = self.stats.get("downloaded", 0)
        skipped = self.stats.get("skipped", 0)
        failed = self.stats.get("failed", 0)
        log.info("")
        log.info("---- Dictionary Download Summary ----")
        log.info("downloaded=%d", downloaded)
        log.info("skipped=%d", skipped)
        log.info("failed=%d", failed)

        if self._errors:
            log.error("")
            log.error(
                "---- Detailed Exception Summary (%d errors) ----",
                len(self._errors),
            )
            for i, (_task, exc) in enumerate(self._errors, 1):
                log.error("[%d] Task failed: %s", i, exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A Sougou dictionary spider.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--directory",
        "-d",
        type=Path,
        help="The directory to save Sougou dictionaries.\n"
        "Default: sougou_dict.",
        metavar="DIR",
    )
    parser.add_argument(
        "--categories",
        "-c",
        nargs="+",
        help="List of category indices to be downloaded.\n"
        "Categories are not separated to their subcategories.\n"
        "Special category 0 is for dictionaries"
        "that do not belong to any categories.\n"
        "Download all categories (including 0) by default.",
        metavar="CATEGORY",
    )
    parser.add_argument(
        "--exclude",
        "-e",
        nargs="+",
        help="List of dictionary indices to exclude downloading.\n"
        "Default: 2775, 15946, 176476 (nonexistent dictionaries)",
        metavar="DICTIONARY",
    )
    parser.add_argument(
        "--concurrent-downloads",
        "-j",
        type=int,
        help="Set the number of parallel downloads.\n"
        "Default: min(32, (os.cpu_count() or 1) * 5)",
        metavar="N",
    )
    parser.add_argument(
        "--max-retries",
        "-m",
        type=int,
        help="Set the maximum number of retries.\nDefault: 20",
        metavar="N",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=float,
        help="Set timeout in seconds.\nDefault: 60",
        metavar="SEC",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Output debug info.\nDefault: False",
    )
    args = parser.parse_args()
    logging.basicConfig(
        format="%(levelname)s:%(message)s",
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    with DictSpider(
        args.categories,
        args.directory,
        args.exclude,
        args.concurrent_downloads,
        args.max_retries,
        args.timeout,
    ) as dict_spider:
        pass
