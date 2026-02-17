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
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

import requests
import tenacity
from bs4 import BeautifulSoup

import queue_thread_pool_executor

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable
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
        self.max_retries = 20 if max_retries is None else max(0, max_retries)
        self.timeout = 60 if timeout is None else timeout
        self.headers = (
            headers
            if headers is not None
            else {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:60.0) "
                    "Gecko/20100101 Firefox/60.0"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": (
                    "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2"
                ),
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )
        self._stats: dict[str, int] = {"downloaded": 0, "skipped": 0, "failed": 0}
        self._thread_local = threading.local()
        self._sessions: list[requests.Session] = []
        self._executor = queue_thread_pool_executor.QueueThreadPoolExecutor(
            max(1, concurrent_downloads or min(32, (os.cpu_count() or 1) * 5))
        )
        self._lock = threading.Lock()
        self._futures: list[Future[object]] = []

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
            RuntimeError: If there were any failures during the download.

        """
        try:
            rv = self._executor.__exit__(typ, exc, tb)
        finally:
            failures = 0
            with contextlib.suppress(Exception):
                failures = self._report_stats()
            with contextlib.suppress(Exception):
                for s in self._sessions:
                    with contextlib.suppress(Exception):
                        s.close()

        if typ is None and failures:
            msg = f"Application finished with {failures} failures."
            raise RuntimeError(msg)

        return rv

    def _submit(
        self, fn: Callable[..., object], /, *args: object, **kwargs: object
    ) -> None:
        with self._lock:
            self._futures.append(self._executor.submit(fn, *args, **kwargs))

    def _get_session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            with self._lock:
                self._sessions.append(session)
            self._thread_local.session = session
        return session

    def _get_html(self, url: str) -> requests.Response:

        def _attempt() -> requests.Response:
            response = self._get_session().get(
                url, headers=self.headers, timeout=self.timeout
            )
            response.raise_for_status()
            if not response.content:
                msg = f"Empty content for {url}"
                raise requests.RequestException(msg)
            return response

        try:
            return tenacity.retry(
                reraise=True,
                stop=tenacity.stop_after_attempt(self.max_retries + 1),
                wait=tenacity.wait_exponential(multiplier=1, max=60),
                retry=tenacity.retry_if_exception_type(requests.RequestException),
                before_sleep=tenacity.before_sleep_log(log, logging.WARNING),
            )(_attempt)()
        except Exception:
            msg = f"Failed to fetch {url}."
            log.exception(msg)
            raise

    def _download(self, name: str, url: str, category_path: Path) -> None:
        file_path = category_path / name
        if file_path.is_file():
            log.warning("%s already exists, skipping...", file_path)
            with self._lock:
                self._stats["skipped"] += 1
            return

        try:
            file_path.write_bytes(self._get_html(url).content)
            with self._lock:
                self._stats["downloaded"] += 1
            log.info("%s downloaded successfully.", name)
        except Exception:
            with self._lock:
                self._stats["failed"] += 1
            raise

    @classmethod
    def _sanitize(cls, raw: str) -> str:
        return raw.translate(
            str.maketrans({"/": "-", ",": "-", "|": "-", "\\": "-", "'": "-"})
        )

    def _download_page(self, page_url: str, category_path: Path) -> None:
        for dict_td in BeautifulSoup(
            self._get_html(page_url).text, "html.parser"
        ).select("div.dict_detail_block"):
            title_div = dict_td.select_one("div.detail_title")
            dict_td_title = title_div.select_one("a") if title_div is not None else None
            dict_td_id = str(
                dict_td_title.get("href") if dict_td_title is not None else None
            ).rpartition("/")[-1]
            if dict_td_id not in self.exclude_list:
                dl_div = dict_td.select_one("div.dict_dl_btn")
                dl_a = dl_div.select_one("a") if dl_div is not None else None
                self._submit(
                    self._download,
                    self._sanitize(
                        (dict_td_title.string if dict_td_title is not None else "")
                        or ""
                    )
                    + "_"
                    + dict_td_id
                    + ".scel",
                    dl_a.get("href") if dl_a is not None else None,
                    category_path,
                )

    def _download_category(
        self,
        category: str,
        category_167: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        category_url = "https://pinyin.sogou.com/dict/cate/index/" + category
        soup = BeautifulSoup(self._get_html(category_url).text, "html.parser")
        if not category_167:
            title_tag = soup.select_one("title")
            title_text = title_tag.get_text(strip=True) if title_tag is not None else ""
            category_path = self.save_path / (
                title_text.partition("_")[0] + "_" + category
            )
            category_path.mkdir(parents=True, exist_ok=True)
        else:
            category_path = self.save_path / "城市信息大全_167"
        page_list = soup.select_one("div#dict_page_list")
        if page_list is None:
            page_n = DictSpider.MIN_PAGE_CATEGORY
        else:
            pages = page_list.select("a")
            if len(pages) < DictSpider.MIN_PAGE_CATEGORY:
                page_n = DictSpider.MIN_PAGE_CATEGORY
            else:
                try:
                    page_n = int(pages[-2].get_text(strip=True)) + 1
                except (TypeError, ValueError):
                    page_n = DictSpider.MIN_PAGE_CATEGORY
        for page in range(1, page_n):
            self._submit(
                self._download_page,
                category_url + "/default/" + str(page),
                category_path,
            )

    def _download_category_167(self) -> None:
        soup = BeautifulSoup(
            self._get_html("https://pinyin.sogou.com/dict/cate/index/180").text,
            "html.parser",
        )
        (self.save_path / "城市信息大全_167").mkdir(parents=True, exist_ok=True)
        for category_td in soup.select("div.citylistcate"):
            a_tag = category_td.select_one("a")
            self._submit(
                self._download_category,
                str(a_tag.get("href") if a_tag is not None else None).rpartition("/")[
                    -1
                ],
                True,  # noqa: FBT003
            )

    def _download_category_0(self) -> None:
        soup = BeautifulSoup(
            self._get_html("https://pinyin.sogou.com/dict/detail/index/4").text,
            "html.parser",
        )
        category_path = self.save_path / "未分类_0"
        category_path.mkdir(parents=True, exist_ok=True)
        self._submit(
            self._download,
            "网络流行新词【官方推荐】_4.scel",
            "https://pinyin.sogou.com/d/dict/download_cell.php?id=4&name=网络流行新词【官方推荐】",
            category_path,
        )
        for dict_td in soup.select("div.rcmd_dict"):
            title_div = dict_td.select_one("div.rcmd_dict_title")
            dict_td_title = title_div.select_one("a") if title_div is not None else None
            dl_div = dict_td.select_one("div.rcmd_dict_dl_btn")
            dl_a = dl_div.select_one("a") if dl_div is not None else None
            self._submit(
                self._download,
                self._sanitize(
                    (dict_td_title.string if dict_td_title is not None else "") or ""
                )
                + "_"
                + str(
                    dict_td_title.get("href") if dict_td_title is not None else None
                ).rpartition("/")[-1]
                + ".scel",
                "https:" + str(dl_a.get("href") if dl_a is not None else None),
                category_path,
            )

    def _download_dicts(self) -> None:
        if self.categories is None:

            def _iter_categories() -> Generator[str]:
                yield "0"
                for category in BeautifulSoup(
                    self._get_html("https://pinyin.sogou.com/dict/").text, "html.parser"
                ).select("div.dict_category_list_title"):
                    a_tag = category.select_one("a")
                    yield (
                        str(a_tag.get("href") if a_tag is not None else "")
                        .partition("?")[0]
                        .rpartition("/")[-1]
                    )

            iterable = _iter_categories()
        else:
            iterable = self.categories
        for category in iterable:
            if category == "0":
                self._submit(self._download_category_0)
            elif category == "167":
                self._submit(self._download_category_167)
            else:
                self._submit(self._download_category, category)

    def _report_stats(self) -> int:
        log.info("")
        log.info("---- Dictionary Download Summary ----")
        log.info("downloaded=%d", self._stats.get("downloaded", 0))
        log.info("skipped=%d", self._stats.get("skipped", 0))
        log.info("failed=%d", self._stats.get("failed", 0))

        log.info("")
        log.info("---- Detailed Exception Summary ----")
        failures = 0
        for future in self._futures:
            exception = future.exception()
            if exception is not None:
                failures += 1
                log.error("[%d] %s", failures, exception)
        if not failures:
            log.info("No exceptions occurred during the download process.")
        return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A Sougou dictionary spider.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--directory",
        "-d",
        type=Path,
        help="The directory to save Sougou dictionaries.\nDefault: sougou_dict.",
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
