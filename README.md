# Warning: Deprecated branch.

Sougou & Baidu Pinyin dictionary for Fcitx5 and RIME.

## Installation

### GitHub Actions
You can run the GitHub Actions workflow to build the dictionaries.

### Manual Build
You need to download Sougou/Baidu dictionaries first with `DictSpider.py` or from release and make sure the dictionaries are put in `sougou_dict`/`baidu_dict` directory.

Build requirement: [>=imewlconverter-3.1.1](https://github.com/studyzy/imewlconverter) (make sure `ImeWlConverterCmd` is added to `PATH`)

**Note: You need <=imewlconverter-3.3.0 to build Baidu dictionaries, see [this issue](https://github.com/studyzy/imewlconverter/issues/381).**

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
