Sougou & Baidu Pinyin dictionary for Fcitx5 and RIME.

Raw Sougou & Baidu pinyin dictionary are automatically updated and released monthly by Github Action with `DictSpider.py` (modified from [Sougou_dict_spider](https://github.com/StuPeter/Sougou_dict_spider)).

Dictionaries are also automatically built and released monthly for fcitx5 and rime by Github Action.

`Makefile` is modified from [fcitx5-pinyin-zhwiki](https://github.com/felixonmars/fcitx5-pinyin-zhwiki).

## Installation

### Pre-built

#### fcitx5
Download latest version of `sougou.dict`/`baidu.dict` from release and copy into `~/.local/share/fcitx5/pinyin/dictionaries/` (create the folder if it does not exist).

#### rime
Download latest version of `sougou.dict.yaml`/`baidu.dict.yaml` from release and copy into `~/.local/share/fcitx5/rime/` for [fcitx5-rime](https://github.com/fcitx/fcitx5-rime) or `~/.config/ibus/rime/` for [ibus-rime](https://github.com/rime/ibus-rime) (create the folder if it does not exist).

### Manual Build
You need to download Sougou/Baidu dictionaries first with `DictSpider.py` or from release and make sure the dictionaries are put in `sougou_dict`/`baidu_dict` directory.

Build requirement: [>=imewlconverter-3.1.1](https://github.com/studyzy/imewlconverter) (make sure `ImeWlConverterCmd` is added to `PATH`)

#### fcitx5
Extra build requirement: [libime](https://github.com/fcitx/libime/)
```
$ make build_fcitx
# make install_fcitx
```

#### rime
```
$ make build_rime
# make install_rime
```

## License
License: Unlicense
