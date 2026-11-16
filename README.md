# Lorcana TCGplayer → Google Sheets

检查 TCGplayer 的 Lorcana `Cards` 分类，按商品 ID 比较 Google 表格，并补录遗漏商品。包含网站归入 Cards 的拼图、插卡、整套等；不会将同一商品的普通/闪卡印刷版本另拆行。

## 一次性配置

1. 在 Google Cloud 项目启用 **Google Sheets API**。
2. 创建服务账号，创建 JSON 密钥。无需授予项目 Owner 权限。
3. 将目标 Google 表格共享给 JSON 中的 `client_email`，授予编辑权限。
4. 在本仓库 **Settings → Secrets and variables → Actions → Secrets** 新建 `GOOGLE_SERVICE_ACCOUNT_JSON`，值为完整 JSON。密钥只能放 Secret，不能提交到仓库。
5. 在同页面 **Variables** 新建 `SPREADSHEET_ID`，值为目标表格网址 `/d/` 和 `/edit` 之间的字符串。

ChatGPT 中 Google Drive 的授权不会自动传递给 GitHub Actions，需要上述一次性配置。

## 每次手动运行

打开 **Actions → Check and update Lorcana → Run workflow**。

- 勾选 `apply`：核查并补录遗漏，更新目录统计。
- 取消 `apply`：只检查，不修改表格。
- 运行后查看 Summary：网站数量、表格数量、缺失商品 ID、仅表格存在的 ID。

没有设置定时执行。工作流的并发锁防止同一仓库同时运行多个更新任务。运行期间请避免手工改动表格；Sheets API 没有跨客户端事务锁。

## 本地运行（Python 3.10+）

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python sync_lorcana.py --spreadsheet-id YOUR_SPREADSHEET_ID
python sync_lorcana.py --spreadsheet-id YOUR_SPREADSHEET_ID --apply
```

本地也需要设置 `GOOGLE_SERVICE_ACCOUNT_JSON` 环境变量。PowerShell 示例：

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content -Raw 'C:\private\service-account.json'
$env:SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID'
python sync_lorcana.py --apply
```

本地结果在 `output/`：`catalog.json`、`missing_rows.json`、`report.json`。该目录已被 Git 忽略。Actions 仅输出检查摘要，不向公共仓库提交表格内容、数据快照或凭据。

## 更新规则

- 明细页：`洛卡纳TCG`；目录页：`洛卡纳TCG目录`；26 列表头必须与脚本定义一致。
- 只追加新商品，不覆盖已有卡牌、备注或价格，不删除仅存在于表格的商品；`洛卡纳Player` 不参与更新。
- 价格是新记录采集时的快照，不是全表实时价格刷新。属性为空时保留为空，不虚构内容。
- 系列代码优先沿用表格同系列的代码（例如 DLPC），新系列使用来源的 setCode。
- 目录保留原有说明、统计公式和采集日期，扩展统计范围，新增“最近完整性核查”日期；新系列插入合计行之前。
- 获取全部系列并分页扫描，验证每组数量、商品 ID 唯一性、分类以及扫描前后的总数。失败不写表。
- 写入前重读表格，检查是否被其他人修改。数据与目录通过一个 batchUpdate 原子提交；未知写入结果不自动重试，重新运行时重新按 ID 比对。
- 每次重跑均重新读取 ID；已补录商品不会重复写入。

## 来源与限制

来源是 TCGplayer 网站当前使用的公开搜索接口：
`https://mp-search-api.tcgplayer.com/v1/search/request`

该接口不是稳定性承诺；接口或网站字段变更时脚本可能停止，需要维护。请求使用每页 50 条、最多 3 个并发和有界重试。完整性仅相对于检查时网站搜索可返回的 Cards 商品集合；不代表所有实体印刷版本，也不证明已有行的每个属性都准确。

遇到 HTTP 403/429 或超时请稍后重跑，不要提高并发。凭据错误检查 Secret、Sheets API 和表格共享权限。新表头、公式、验证或智能芯片结构会触发安全停止，需要检查映射。

参考：[Sheets batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)、[GitHub 手动运行工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。
