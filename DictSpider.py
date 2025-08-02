#!/usr/bin/env python3
#
# Modified from https://github.com/StuPeter/Sougou_dict_spider/blob/master/SougouSpider.py
#
# See the LICENSE file for more information.

"""
DictSpider: A spider for downloading Sougou and Baidu dictionaries.

This module provides the DictSpider class, which can download and organize
dictionaries from Sougou and Baidu input method websites, supporting
parallel downloads, exclusion lists, and category selection.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

import queue_thread_pool

if TYPE_CHECKING:
    from types import TracebackType

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class DictSpider:
    """
    A spider for downloading Sougou and Baidu input method dictionaries.

    Attributes
    ----------
    sougou_save_path : Path
        Directory to save Sougou dictionaries.
    sougou_exclude_list : set[str]
        Set of Sougou dictionary indices to exclude.
    baidu_save_path : Path
        Directory to save Baidu dictionaries.
    baidu_exclude_list : set[str]
        Set of Baidu dictionary inner ids to exclude.
    max_retries : int
        Maximum number of retries for HTTP requests.
    timeout : float
        Timeout for HTTP requests in seconds.
    headers : dict[str, str]
        HTTP headers to use for requests.

    Methods
    -------
    download_dicts(sougou_categories, baidu_categories)
        Download dictionaries from Sougou and Baidu.

    """

    MIN_PAGE_CATEGORY: Final = 2

    def __init__(
        self,
        sougou_save_path: Path = Path("sougou_dict"),
        sougou_exclude_list: set[str] | None = None,
        baidu_save_path: Path = Path("baidu_dict"),
        baidu_exclude_list: set[str] | None = None,
        concurrent_downloads: int = (os.cpu_count() or 1) * 2,
        max_retries: int = 10,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize the DictSpider.

        Args:
            sougou_save_path (Path): Directory to save Sougou dictionaries.
            sougou_exclude_list (set[str] | None): Set of Sougou dictionary
            indices to exclude.
            baidu_save_path (Path): Directory to save Baidu dictionaries.
            baidu_exclude_list (set[str] | None): Set of Baidu dictionary
            inner ids to exclude.
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
        if baidu_exclude_list is None:
            baidu_exclude_list = {"4206105738"}
        if sougou_exclude_list is None:
            sougou_exclude_list = {"2775", "15946", "15233"}
        self.sougou_save_path = sougou_save_path
        self.sougou_exclude_list = sougou_exclude_list
        self.baidu_save_path = baidu_save_path
        self.baidu_exclude_list = baidu_exclude_list
        self.max_retries = max_retries
        self.timeout = timeout
        self.headers = headers
        self.__executor = queue_thread_pool.QueueThreadPool(
            concurrent_downloads
        )

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
        return self.__executor.__exit__(typ, exc, tb)

    def __get_html(self, url: str) -> requests.Response:
        with requests.Session() as session:
            session.mount(
                "https://",
                HTTPAdapter(
                    max_retries=Retry(
                        total=self.max_retries, backoff_factor=0.1
                    )
                ),
            )
            retries = 0
            while (
                not (
                    response := session.get(
                        url, headers=self.headers, timeout=self.timeout
                    )
                ).content
                and retries < self.max_retries
            ):
                log.debug("The content of %s is empty.", url)
                retries += 1
            if retries == self.max_retries:
                # For dictionaries like 医学八_3183610510.bdict
                log.warning("The content of %s is empty!", url)
            return response

    def __download(self, name: str, url: str, category_path: Path) -> None:
        if not name.rpartition("_")[0]:
            log.warning("%s has an empty name!", name)
        file_path = category_path / name
        if file_path.is_file():
            log.warning("%s already exists, skipping...", file_path)
            return
        content = self.__get_html(url).content
        if not content:
            # For dictionaries like 医学八_3183610510.bdict
            log.warning("%s is empty, skipping...", name)
            return
        file_path.write_bytes(content)
        log.info("%s downloaded successfully.", name)

    def __sougou_download_page(
        self, page_url: str, category_path: Path
    ) -> None:
        for dict_td in BeautifulSoup(
            self.__get_html(page_url).text, "html.parser"
        ).find_all("div", class_="dict_detail_block"):
            if (
                dict_td_id := (
                    dict_td_title := dict_td.find(
                        "div", class_="detail_title"
                    ).a
                )["href"].rpartition("/")[-1]
            ) not in self.sougou_exclude_list:
                self.__executor.submit(
                    self.__download,
                    (
                        # For dictionaries like 天线行业/BSA_67002
                        dict_td_title.string.replace("/", "-")
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

    def __sougou_download_category(
        self,
        category: str,
        category_167: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        category_url = "https://pinyin.sogou.com/dict/cate/index/" + category
        soup = BeautifulSoup(self.__get_html(category_url).text, "html.parser")
        if not category_167:
            category_path = self.sougou_save_path / (
                soup.find("title").string.partition("_")[0] + "_" + category
            )
            category_path.mkdir(parents=True, exist_ok=True)
        else:
            category_path = self.sougou_save_path / "城市信息大全_167"
        page_n = (
            DictSpider.MIN_PAGE_CATEGORY
            if (page_list := soup.find("div", id="dict_page_list")) is None
            or len(pages := page_list.find_all("a"))
            < DictSpider.MIN_PAGE_CATEGORY
            else int(pages[-2].string) + 1
        )
        for page in range(1, page_n):
            self.__executor.submit(
                self.__sougou_download_page,
                category_url + "/default/" + str(page),
                category_path,
            )

    def __sougou_download_category_167(self) -> None:
        """For category 167 that does not have a page."""
        category_path = self.sougou_save_path / "城市信息大全_167"
        category_path.mkdir(parents=True, exist_ok=True)
        for category_td in BeautifulSoup(
            self.__get_html(
                "https://pinyin.sogou.com/dict/cate/index/180"
            ).text,
            "html.parser",
        ).find_all("div", class_="citylistcate"):
            self.__executor.submit(
                self.__sougou_download_category,
                category_td.a["href"].rpartition("/")[-1],
                True,  # noqa: FBT003
            )

    def __sougou_download_category_0(self) -> None:
        """For dictionaries that do not belong to any categories."""
        category_path = self.sougou_save_path / "未分类_0"
        category_path.mkdir(parents=True, exist_ok=True)
        self.__executor.submit(
            self.__download,
            "网络流行新词【官方推荐】_4.scel",
            "https://pinyin.sogou.com/d/dict/download_cell.php?id=4&name=网络流行新词【官方推荐】",
            category_path,
        )
        for dict_td in BeautifulSoup(
            self.__get_html(
                "https://pinyin.sogou.com/dict/detail/index/4"
            ).text,
            "html.parser",
        ).find_all("div", class_="rcmd_dict"):
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

    def __sougou_download_dicts(self, categories: set[str] | None) -> None:
        for category in (
            itertools.chain(
                ["0"],
                (
                    category.a["href"].partition("?")[0].rpartition("/")[-1]
                    for category in BeautifulSoup(
                        self.__get_html("https://pinyin.sogou.com/dict/").text,
                        "html.parser",
                    ).find_all("div", class_="dict_category_list_title")
                ),
            )
            if categories is None
            else categories
        ):
            if category == "0":
                self.__executor.submit(self.__sougou_download_category_0)
            elif category == "167":
                self.__executor.submit(self.__sougou_download_category_167)
            else:
                self.__executor.submit(
                    self.__sougou_download_category, category
                )

    def __baidu_download_page(
        self, page_url: str, category_path: Path
    ) -> None:
        for dict_td in BeautifulSoup(
            self.__get_html(page_url).text, "html.parser"
        ).find_all(
            "a",
            href="javascript:void(0)",
            class_="dict-down dictClick",
            title="立即下载",
        ):
            if (
                dict_td_id := dict_td["dict-innerid"]
            ) not in self.baidu_exclude_list:
                self.__executor.submit(
                    self.__download,
                    # For dictionaries like 汽车常用词/术语_3132361350
                    dict_td["dict-name"].replace("/", "-")
                    + "_"
                    + dict_td_id
                    + ".bdict",
                    "https://shurufa.baidu.com/dict_innerid_download?innerid="
                    + dict_td_id,
                    category_path,
                )

    def __baidu_download_category(self, category: str) -> None:
        category_url = "https://shurufa.baidu.com/dict_list?cid=" + category
        soup = BeautifulSoup(self.__get_html(category_url).text, "html.parser")
        category_path = self.baidu_save_path / (
            soup.find("title").string.rpartition("-")[-1] + "_" + category
        )
        category_path.mkdir(parents=True, exist_ok=True)
        page_n = (
            2
            if (
                pages := soup.find_all(
                    "a",
                    href=re.compile(r"dict_list\?cid=(\d+)&page=(\d+)#page"),
                )
            )
            is None
            or len(pages) < DictSpider.MIN_PAGE_CATEGORY
            else int(pages[-2].string) + 1
        )
        for page in range(1, page_n):
            self.__executor.submit(
                self.__baidu_download_page,
                category_url + "&page=" + str(page),
                category_path,
            )

    def __baidu_download_dicts(self, categories: set[str] | None) -> None:
        for category in (
            (
                category["href"].partition("=")[-1]
                for category in BeautifulSoup(
                    self.__get_html("https://shurufa.baidu.com/dict").text,
                    "html.parser",
                ).find_all(
                    "a", attrs={"data-stats": "webDictPage.dictSort.category1"}
                )
            )
            if categories is None
            else categories
        ):
            self.__executor.submit(self.__baidu_download_category, category)

    def download_dicts(
        self,
        sougou_categories: set[str] | None = None,
        baidu_categories: set[str] | None = None,
    ) -> None:
        """
        Download dictionaries from Sougou and Baidu.

        Args:
            sougou_categories (set[str] | None): Set of Sougou category indices
            to download.
            baidu_categories (set[str] | None): Set of Baidu category indices
            to download.

        """
        self.__executor.submit(self.__sougou_download_dicts, sougou_categories)
        self.__executor.submit(self.__baidu_download_dicts, baidu_categories)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A Sougou & Baidu dictionary spider.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--sougou",
        "-S",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Download Sougou dictionaries.\nDefault: True",
    )
    parser.add_argument(
        "--baidu",
        "-B",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Download Baidu dictionaries.\nDefault: True",
    )
    parser.add_argument(
        "--sougou_directory",
        "-d",
        default="sougou_dict",
        type=Path,
        help="The directory to save Sougou dictionaries.\n"
        "Default: sougou_dict.",
        metavar="DIR",
    )
    parser.add_argument(
        "--baidu_directory",
        "-D",
        default="baidu_dict",
        type=Path,
        help="The directory to save Baidu dictionaries.\nDefault: baidu_dict.",
        metavar="DIR",
    )
    parser.add_argument(
        "--sougou_categories",
        "-c",
        nargs="+",
        help="List of Sougou category indexes to be downloaded.\n"
        "Categories are not separated to their subcategories.\n"
        "Special category 0 is for dictionaries"
        "that do not belong to any categories.\n"
        "Download all categories (including 0) by default.",
        metavar="CATEGORY",
    )
    parser.add_argument(
        "--baidu_categories",
        "-C",
        nargs="+",
        help="List of Baidu category indexes to be downloaded.\n"
        "Categories are not separated to their subcategories.\n"
        "Download all categories by default.",
        metavar="CATEGORY",
    )
    parser.add_argument(
        "--sougou_exclude",
        "-e",
        default=["2775", "15946", "15233"],
        nargs="+",
        help="List of Sougou dictionary indexes to exclude downloading.\n"
        "Default: 2775 (威海地名): nonexistent dictionary\n"
        "	 15946: nonexistent dictionary"
        "	 15233: nonexistent dictionary",
        metavar="DICTIONARY",
    )
    parser.add_argument(
        "--baidu_exclude",
        "-E",
        default=["4206105738"],
        nargs="+",
        help="List of Baidu dictionary inner ids to exclude downloading.\n"
        "Default: 4206105738 (互猎网): page 404",
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
        help="Set the maximum number of retries.\nDefault: 5",
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
        args.sougou_directory,
        set(args.sougou_exclude),
        args.baidu_directory,
        set(args.baidu_exclude),
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
            set()
            if not args.sougou
            else None
            if args.sougou_categories is None
            else set(args.sougou_categories),
            set()
            if not args.baidu
            else None
            if args.baidu_categories is None
            else set(args.baidu_categories),
        )
