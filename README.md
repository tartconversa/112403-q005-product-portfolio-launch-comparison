# 多品类新品成长基准与资源转配评审

合成新品商品谱系、渠道可售期间、净交易和内容资源样例。

All example identifiers and values are synthetic. CSV files use UTF-8. The first row contains column names, and an empty cell means the source supplied no value.

## Sample files

- `products.csv` contains 新品、前代及商品版本的谱系关系。 Columns are `sku`, `category`, `predecessor`, `version`, `launch`.
- `availability.csv` contains 商品在渠道的有效上架和可售天数。 Columns are `sku`, `channel`, `from`, `to`, `days`, `reason`.
- `performance.csv` contains 商品阶段净购买和内容资源投入。 Columns are `sku`, `window`, `units`, `refunds`, `spend`.

Python 3 standard library is sufficient to inspect these inputs. Run `python3 -m unittest discover -s tests -v` to check the delivered file structures and example references.
