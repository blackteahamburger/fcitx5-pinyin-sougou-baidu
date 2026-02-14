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
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

import queue_thread_pool

if TYPE_CHECKING:
    from types import TracebackType

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class DictSpider:
    """
    A spider for downloading Sougou dictionaries.

    Attributes
    ----------
    save_path : Path
        Directory to save dictionaries.
    exclude_list : set[str]
        Set of dictionary indices to exclude.
    max_retries : int
        Maximum number of retries for HTTP requests.
    timeout : float
        Timeout for HTTP requests in seconds.
    headers : dict[str, str]
        HTTP headers to use for requests.

    Methods
    -------
    download_dicts(categories)
        Download dictionaries.

    """

    MIN_PAGE_CATEGORY: Final = 2

    def __init__(
        self,
        save_path: Path = Path("sougou_dict"),
        exclude_list: set[str] | None = None,
        concurrent_downloads: int = (os.cpu_count() or 1) * 2,
        max_retries: int = 10,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize the DictSpider.

        Args:
            save_path (Path): Directory to save dictionaries.
            exclude_list (set[str] | None):
                Set of dictionary indices to exclude.
            concurrent_downloads (int): Number of parallel downloads.
            max_retries (int): Maximum number of retries for HTTP requests.
            timeout (float): Timeout for HTTP requests in seconds.
            headers (dict[str, str] | None): HTTP headers to use for requests.

        """
        if headers is None:
            headers = {
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
        if exclude_list is None:
            exclude_list = {"2775", "15946", "15233"}
        self.save_path = save_path
        self.exclude_list = exclude_list
        self.max_retries = max_retries
        self.timeout = timeout
        self.headers = headers
        self.__session = requests.Session()
        pool_size = max(10, concurrent_downloads)
        adapter = HTTPAdapter(
            pool_connections=pool_size, pool_maxsize=pool_size
        )
        self.__session.mount("https://", adapter)
        self.__session.mount("http://", adapter)

        self.__executor = queue_thread_pool.QueueThreadPool(
            concurrent_downloads
        )
        self.__lock = threading.Lock()
        self.__stats: dict[str, int] = {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
        }
        self.__failed_downloads: list[str] = []

    def __enter__(self) -> Self:
        """
        Enter the runtime context related to this object.

        Returns:
            Self: The DictSpider instance itself.

        """
        self.__executor.__enter__()
        return self

    def __exit__(
        self,
        typ: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """
        Exit the runtime context and clean up resources.

        Args:
            typ (type[BaseException] | None): Exception type, if any.
            exc (BaseException | None): Exception instance, if any.
            tb (TracebackType | None): Traceback, if any.

        """
        try:
            return self.__executor.__exit__(typ, exc, tb)
        finally:
            with contextlib.suppress(Exception):
                self.report_stats()
            with contextlib.suppress(Exception):
                self.__session.close()

    def __get_html(self, url: str) -> requests.Response | None:
        for attempt in range(max(1, self.max_retries)):
            try:
                response = self.__session.get(
                    url, headers=self.headers, timeout=self.timeout
                )
                response.raise_for_status()
                if response.content:
                    return response
                log.warning(
                    "Downloaded content of %s is empty (attempt %d/%d)",
                    url,
                    attempt + 1,
                    self.max_retries,
                )
            except requests.RequestException as exc:
                log.warning(
                    "Request failed for %s (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
            backoff = 0.1 * (2**attempt)
            time.sleep(backoff)
        msg = (
            f"Downloaded content of {url} is empty or failed after "
            f"{self.max_retries} attempts"
        )
        with self.__lock:
            self.__failed_downloads.append(url)
        log.error(msg)
        # Do not raise here to avoid terminating the whole program; callers
        # should handle a None return value and continue.
        return None

    def __download(self, name: str, url: str, category_path: Path) -> None:
        file_path = category_path / name
        with self.__lock:
            if file_path.is_file():
                log.warning("%s already exists, skipping...", file_path)
                self.__stats["skipped"] += 1
                return
        response = self.__get_html(url)
        if response is None:
            with self.__lock:
                self.__stats["failed"] += 1
            log.error("Failed to fetch file URL: %s (will skip %s)", url, name)
            return
        content = response.content
        file_path.write_bytes(content)
        with self.__lock:
            self.__stats["downloaded"] += 1
        log.info("%s downloaded successfully.", name)

    def __download_page(self, page_url: str, category_path: Path) -> None:
        response = self.__get_html(page_url)
        if response is None:
            log.error("Failed to fetch page URL: %s", page_url)
            return
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
                self.__executor.submit(
                    self.__download,
                    (
                        # For dictionaries like 天线行业/BSA_67002
                        dict_td_title.string
                        .replace("/", "-")
                        .replace(",", "-")
                        .replace("|", "-")
                        .replace("\\", "-")
                        .replace("'", "-")
                        if dict_td_title.string
                        else ""  # For dictionaries without a name
                    )
                    + "_"
                    + dict_td_id
                    + ".scel",
                    dict_td.find("div", class_="dict_dl_btn").a["href"],
                    category_path,
                )

    def __download_category(
        self,
        category: str,
        category_167: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        category_url = "https://pinyin.sogou.com/dict/cate/index/" + category
        response = self.__get_html(category_url)
        if response is None:
            log.error("Failed to fetch category URL: %s", category_url)
            return
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
            self.__executor.submit(
                self.__download_page,
                category_url + "/default/" + str(page),
                category_path,
            )

    def __download_category_167(self) -> None:
        """For category 167 that does not have a page."""
        category_url = "https://pinyin.sogou.com/dict/cate/index/180"
        response = self.__get_html(category_url)
        if response is None:
            log.error("Failed to fetch category URL: %s", category_url)
            return
        category_path = self.save_path / "城市信息大全_167"
        category_path.mkdir(parents=True, exist_ok=True)
        soup = BeautifulSoup(response.text, "html.parser")
        for category_td in soup.find_all("div", class_="citylistcate"):
            self.__executor.submit(
                self.__download_category,
                category_td.a["href"].rpartition("/")[-1],
                True,  # noqa: FBT003
            )

    def __download_category_0(self) -> None:
        """For dictionaries that do not belong to any categories."""
        category_url = "https://pinyin.sogou.com/dict/detail/index/4"
        response = self.__get_html(category_url)
        if response is None:
            log.error("Failed to fetch category URL: %s", category_url)
            return
        category_path = self.save_path / "未分类_0"
        category_path.mkdir(parents=True, exist_ok=True)
        self.__executor.submit(
            self.__download,
            "网络流行新词【官方推荐】_4.scel",
            "https://pinyin.sogou.com/d/dict/download_cell.php?id=4&name=网络流行新词【官方推荐】",
            category_path,
        )
        for dict_td in BeautifulSoup(response.text, "html.parser").find_all(
            "div", class_="rcmd_dict"
        ):
            self.__executor.submit(
                self.__download,
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

    def __download_dicts(self, categories: set[str] | None) -> None:
        if categories is None:
            main_url = "https://pinyin.sogou.com/dict/"
            response = self.__get_html(main_url)
            if response is None:
                log.error("Failed to fetch main index URL: %s", main_url)
                return
            soup = BeautifulSoup(response.text, "html.parser")
            category_iter = (
                category.a["href"].partition("?")[0].rpartition("/")[-1]
                for category in soup.find_all(
                    "div", class_="dict_category_list_title"
                )
            )
            iterable = itertools.chain(["0"], category_iter)
        else:
            iterable = categories

        for category in iterable:
            if category == "0":
                self.__executor.submit(self.__download_category_0)
            elif category == "167":
                self.__executor.submit(self.__download_category_167)
            else:
                self.__executor.submit(self.__download_category, category)

    def download_dicts(self, categories: set[str] | None = None) -> None:
        """
        Download dictionaries.

        Args:
            categories (set[str] | None): Set of category indices to download.

        """
        self.__executor.submit(self.__download_dicts, categories)

    def report_stats(self) -> None:
        """Log a summary of download statistics."""
        with self.__lock:
            downloaded = self.__stats.get("downloaded", 0)
            skipped = self.__stats.get("skipped", 0)
            failed = self.__stats.get("failed", 0)
        log.info("")
        log.info("---- Dictionary Download Summary ----")
        log.info("downloaded=%d", downloaded)
        log.info("skipped=%d", skipped)
        log.info("failed=%d", failed)
        if self.__failed_downloads:
            log.error("")
            log.error("---- Failed Downloads Summary ----")
            for url in self.__failed_downloads:
                log.error(url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A Sougou dictionary spider.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--directory",
        "-d",
        default="sougou_dict",
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
        default=["2775", "15946", "15233"],
        nargs="+",
        help="List of dictionary indexes to exclude downloading.\n"
        "Default: 2775 (威海地名): nonexistent dictionary\n"
        "	 15946: nonexistent dictionary"
        "	 15233: nonexistent dictionary",
        metavar="DICTIONARY",
    )
    parser.add_argument(
        "--concurrent-downloads",
        "-j",
        default=(os.cpu_count() or 1) * 2,
        type=int,
        help="Set the number of parallel downloads.\n"
        "Default: os.cpu_count() * 2",
        metavar="N",
    )
    parser.add_argument(
        "--max-retries",
        "-m",
        default=10,
        type=int,
        help="Set the maximum number of retries.\nDefault: 10",
        metavar="N",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        default=60,
        type=float,
        help="Set timeout in seconds.\nDefault: 60",
        metavar="SEC",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Verbose output.\nDefault: False",
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Output debug info.\nDefault: False",
    )
    args = parser.parse_args()
    with DictSpider(
        args.directory,
        set(args.exclude),
        args.concurrent_downloads,
        args.max_retries,
        args.timeout,
    ) as dict_spider:
        logging.basicConfig(
            format="%(levelname)s:%(message)s",
            level=logging.DEBUG
            if args.debug
            else logging.INFO
            if args.verbose
            else logging.WARNING,
        )
        dict_spider.download_dicts(
            None if args.categories is None else set(args.categories)
        )
