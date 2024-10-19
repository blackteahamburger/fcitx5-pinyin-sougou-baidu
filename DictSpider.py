import argparse
import concurrent.futures
import itertools
import logging
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class DictSpider:
	def __init__(
		self,
		sougou_save_path: Path = Path("sougou_dict"),
		sougou_exclude_list: set[str] = set(),
		baidu_save_path: Path = Path("baidu_dict"),
		baidu_exclude_list: set[str] = {"4206105738"},
		concurrent_downloads: int = os.cpu_count() * 2,
		max_retries: int = 5,
		timeout: float = 60.0,
		headers: dict[str, str] = {
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:60.0) Gecko/20100101 Firefox/60.0",
			"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
			"Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
			"Accept-Encoding": "gzip, deflate",
			"Connection": "keep-alive",
		},
	):
		self.sougou_save_path = sougou_save_path
		self.sougou_exclude_list = sougou_exclude_list
		self.baidu_save_path = baidu_save_path
		self.baidu_exclude_list = baidu_exclude_list
		self.max_retries = max_retries
		self.timeout = timeout
		self.headers = headers
		self.__executor = concurrent.futures.ThreadPoolExecutor(concurrent_downloads)
		self.__futures: list[concurrent.futures.Future] = []

	def __enter__(self):
		self.__executor.__enter__()
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		return self.__executor.__exit__(exc_type, exc_val, exc_tb)

	def __get_html(self, url: str):
		with requests.Session() as session:
			session.mount("https://", HTTPAdapter(max_retries=self.max_retries))
			return session.get(url, headers=self.headers, timeout=self.timeout)

	def __download(self, name: str, url: str, category_path: Path):
		file_path = category_path / name
		if file_path.is_file():
			log.warning(f"{file_path} already exists, skipping...")
			return
		retries = 0
		while not (content := self.__get_html(url).content):
			if retries == 5:
				# For dictionaries like 威海地名
				log.warning(f"{name} is empty, skipping...")
				return
			retries += 1
		file_path.write_bytes(content)
		log.info(f"{name} downloaded successfully.")

	def __sougou_download_page(self, page_url: str, category_path: Path):
		self.__futures.extend(
			self.__executor.submit(
				self.__download,
				(
					# For dictionaries like 天线行业/BSA
					dict_td_title.string.replace("/", "-")
					.replace(",", "-")
					.replace("|", "-")
					.replace("\\", "-")
					.replace("'", "-")
					if dict_td_title.string
					else ""  # For dictionaries without a name like index 15946
				)
				+ "_"
				+ dict_td_id
				+ ".scel",
				dict_td.find("div", class_="dict_dl_btn").a["href"],
				category_path,
			)
			for dict_td in BeautifulSoup(
				self.__get_html(page_url).text, "html.parser"
			).find_all("div", class_="dict_detail_block")
			if (
				dict_td_id := (
					dict_td_title := dict_td.find("div", class_="detail_title").a
				)["href"].rpartition("/")[-1]
			)
			not in self.sougou_exclude_list
		)

	def __sougou_download_category(self, category: str, category_167: bool = False):
		category_url = "https://pinyin.sogou.com/dict/cate/index/" + category
		soup = BeautifulSoup(self.__get_html(category_url).text, "html.parser")
		if not category_167:
			category_path = self.sougou_save_path / (
				soup.find("title").string.partition("_")[0] + "_" + category
			)
			category_path.mkdir(exist_ok=True)
		else:
			category_path = self.sougou_save_path / "城市信息大全_167"
		page_n = (
			2
			if (page_list := soup.find("div", id="dict_page_list")) is None
			or len(pages := page_list.find_all("a")) < 2
			else int(pages[-2].string) + 1
		)
		self.__futures.extend(
			self.__executor.submit(
				self.__sougou_download_page,
				category_url + "/default/" + str(page),
				category_path,
			)
			for page in range(1, page_n)
		)

	def __sougou_download_category_167(self):
		"""For category 167 that does not have a page"""
		category_path = self.sougou_save_path / "城市信息大全_167"
		category_path.mkdir(exist_ok=True)
		self.__futures.extend(
			self.__executor.submit(
				self.__sougou_download_category,
				category_td.a["href"].rpartition("/")[-1],
				True,
			)
			for category_td in BeautifulSoup(
				self.__get_html("https://pinyin.sogou.com/dict/cate/index/180").text,
				"html.parser",
			).find_all("div", class_="citylistcate")
		)

	def __sougou_download_category_0(self):
		"""For dictionaries that do not belong to any categories"""
		category_path = self.sougou_save_path / "未分类_0"
		category_path.mkdir(exist_ok=True)
		self.__futures.append(
			self.__executor.submit(
				self.__download,
				"网络流行新词【官方推荐】_4.scel",
				"https://pinyin.sogou.com/d/dict/download_cell.php?id=4&name=网络流行新词【官方推荐】",
				category_path,
			)
		)
		self.__futures.extend(
			self.__executor.submit(
				self.__download,
				(
					dict_td_title := dict_td.find("div", class_="rcmd_dict_title").a
				).string
				+ "_"
				+ dict_td_title["href"].rpartition("/")[-1]
				+ ".scel",
				"https:" + dict_td.find("div", class_="rcmd_dict_dl_btn").a["href"],
				category_path,
			)
			for dict_td in BeautifulSoup(
				self.__get_html("https://pinyin.sogou.com/dict/detail/index/4").text,
				"html.parser",
			).find_all("div", class_="rcmd_dict")
		)

	def __sougou_download_dicts(self, categories: set[str] | None):
		self.sougou_save_path.mkdir(parents=True, exist_ok=True)
		self.__futures.extend(
			self.__executor.submit(self.__sougou_download_category_0)
			if category == "0"
			else self.__executor.submit(self.__sougou_download_category_167)
			if category == "167"
			else self.__executor.submit(self.__sougou_download_category, category)
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
			)
		)

	def __baidu_download_page(self, page_url: str, category_path: Path):
		self.__futures.extend(
			self.__executor.submit(
				self.__download,
				# For dictionaries like 汽车常用词/术语
				dict_td["dict-name"].replace("/", "-") + "_" + dict_td_id + ".bdict",
				"https://shurufa.baidu.com/dict_innerid_download?innerid=" + dict_td_id,
				category_path,
			)
			for dict_td in BeautifulSoup(
				self.__get_html(page_url).text, "html.parser"
			).find_all(
				"a",
				href="javascript:void(0)",
				class_="dict-down dictClick",
				title="立即下载",
			)
			if (dict_td_id := dict_td["dict-innerid"]) not in self.baidu_exclude_list
		)

	def __baidu_download_category(self, category: str):
		category_url = "https://shurufa.baidu.com/dict_list?cid=" + category
		soup = BeautifulSoup(self.__get_html(category_url).text, "html.parser")
		category_path = self.baidu_save_path / (
			soup.find("title").string.rpartition("-")[-1] + "_" + category
		)
		category_path.mkdir(exist_ok=True)
		page_n = (
			2
			if (
				pages := soup.find_all(
					"a", href=re.compile(r"dict_list\?cid=(\d+)&page=(\d+)#page")
				)
			)
			is None
			or len(pages) < 2
			else int(pages[-2].string) + 1
		)
		self.__futures.extend(
			self.__executor.submit(
				self.__baidu_download_page,
				category_url + "&page=" + str(page),
				category_path,
			)
			for page in range(1, page_n)
		)

	def __baidu_download_dicts(self, categories: set[str] | None):
		self.baidu_save_path.mkdir(parents=True, exist_ok=True)
		self.__futures.extend(
			self.__executor.submit(self.__baidu_download_category, category)
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
			)
		)

	def download_dicts(
		self,
		sougou_categories: set[str] | None = None,
		baidu_categories: set[str] | None = None,
	):
		self.__futures = [
			self.__executor.submit(self.__sougou_download_dicts, sougou_categories),
			self.__executor.submit(self.__baidu_download_dicts, baidu_categories),
		]
		for future in self.__futures:
			future.result()


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
		help="Download Sougou dictionaries.\n" "Default: True",
	)
	parser.add_argument(
		"--baidu",
		"-B",
		action=argparse.BooleanOptionalAction,
		default=True,
		help="Download Baidu dictionaries.\n" "Default: True",
	)
	parser.add_argument(
		"--sougou_directory",
		"-d",
		default="sougou_dict",
		type=Path,
		help="The directory to save Sougou dictionaries.\n" "Default: sougou_dict.",
		metavar="DIR",
	)
	parser.add_argument(
		"--baidu_directory",
		"-D",
		default="baidu_dict",
		type=Path,
		help="The directory to save Baidu dictionaries.\n" "Default: baidu_dict.",
		metavar="DIR",
	)
	parser.add_argument(
		"--sougou_categories",
		"-c",
		nargs="+",
		help="List of Sougou category indexes to be downloaded.\n"
		"Categories are not separated to their subcategories.\n"
		"Special category 0 is for dictionaries that do not belong to any categories.\n"
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
		default=[],
		nargs="+",
		help="List of Sougou dictionary indexes to exclude downloading.\n"
		"Default: None.\n",
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
		default=os.cpu_count() * 2,
		type=int,
		help="Set the number of parallel downloads.\n" "Default: os.cpu_count() * 2",
		metavar="N",
	)
	parser.add_argument(
		"--max-retries",
		"-m",
		default=5,
		type=int,
		help="Set the maximum number of retries.\n" "Default: 5",
		metavar="N",
	)
	parser.add_argument(
		"--timeout",
		"-t",
		default=60,
		type=float,
		help="Set timeout in seconds.\n" "Default: 60",
		metavar="SEC",
	)
	parser.add_argument(
		"--verbose",
		"-v",
		action=argparse.BooleanOptionalAction,
		default=False,
		help="Verbose output.\n" "Default: False",
	)
	parser.add_argument(
		"--debug",
		action=argparse.BooleanOptionalAction,
		default=False,
		help="Output debug info.\n" "Default: False",
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
	) as SGSpider:
		logging.basicConfig(
			format="%(levelname)s:%(message)s",
			level=logging.DEBUG
			if args.debug
			else logging.INFO
			if args.verbose
			else logging.WARNING,
		)
		SGSpider.download_dicts(
			[]
			if not args.sougou
			else None
			if args.sougou_categories is None
			else set(args.sougou_categories),
			[]
			if not args.baidu
			else None
			if args.baidu_categories is None
			else set(args.baidu_categories),
		)
