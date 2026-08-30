A Translation and Crawler bot written for crawling websites and translating almost any language with the purpose of making life easier.

## Translation engines

`/translate` supports picking a translation engine at runtime (`Auto`, `Default`, or a specific engine like `GoogleTrans`/`Bing`/`Baidu`/...), and admins can change the site-wide default with `/set_translation_engine`. See [docs/translation_engines.md](docs/translation_engines.md) for the full engine list, which ones need a free API key, and performance tuning options.

