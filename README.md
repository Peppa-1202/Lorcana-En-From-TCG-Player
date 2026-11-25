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

本地结果在 `output/`：`catalog.json`、`missing_rows.json`、`report.json`。该目录已被 Git 忽略。Actions 输出检查摘要，并提供保留 30 天的 lorcana-audit 运行附件（包括目录快照及 OCR 结果）；不把运行数据或凭据提交到 Git。

## 更新规则

- 明细页：`洛卡纳TCG`；目录页：`洛卡纳TCG目录`；原 26 列表头必须与脚本定义一致；首次运行自动增加 AA 列“卡牌代码”，并增加 AB 列“促销卡系列代码”，之后为 28 列。
- 追加新商品，并补充 Promo 的“卡牌代码”；不覆盖已有卡牌属性、备注或价格，不删除仅存在于表格的商品；`洛卡纳Player` 不参与更新。
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

## Promo 图片代码与子类汇总

每次成功执行 `--apply` 会同步更新明细和目录，无需另运行汇总脚本。已配置的 Google Secret/Variable 可以继续使用。

- AA 列 **卡牌代码** 保存图片左下角完整代码，例如 `20/C2·EN·10`、`24A/P2·EN·7`。P1/P2/P3/P4、C1/C2/C3、CC1/CC2、PD1、DIS、RPH 等按图片实际前缀自动分组，不预设固定清单。
- 只处理 `Disney Lorcana Promo Cards`。其他系列的 AA 列留空。语言依图片保留 EN/JA/ZH 等，不根据商品页面语言改写。
- 目录在 DLPC 父行下插入浅蓝色子类行。父行包含全部 Promo 商品；总计只计明细商品 ID，子类不会重复相加。不同商品 ID 使用相同代码时仍各计一次。
- 组合商品在同一格用英文分号保存多个代码。同子类组合只计一条商品；不拆成多行。仅印有 `JA·14` 等语言/版次的商品归“无编号”。
- 已人工核对的 217 条公开卡图记录保存在 `promo_codes_reviewed.json`，含图片 SHA-256；首次填充时必须图片哈希一致才复用。后续保留表格中已有效填写的代码（也保留人工修正），只重试空白、格式无效或“待核查”的行。若需重新识别已填代码，请先清空对应 AA 单元格。
- 新图片使用本地 Tesseract，多次裁剪识别一致且格式完整才接受；反光、低清、遮挡、特殊排版可能识别失败，写为“待核查”。不会用卡名或网页编号猜测。当前 Stand Out（714566）图片反光，仍需人工确认。
- 单元格备注记录识别来源；运行 Summary 显示识别数、待核查数及子类数。`output/promo_ocr.json` 保存 OCR 原文、错误和图片哈希供复核。无法识别不阻断其他完整商品的补录。

GitHub Actions 自动安装 Tesseract，无需额外密钥。本地运行需先安装 **Tesseract OCR（含 eng 数据）** 并让 `tesseract` 在 PATH 中，例如 Ubuntu：`sudo apt-get install tesseract-ocr tesseract-ocr-eng`。Pillow 随 requirements 安装。

识别结果是图片辅助转录，不保证未来所有图片自动识别成功；待核查数量会明确保留在目录中。

## 促销卡系列代码列

AB 列“促销卡系列代码”由 AA 列完整卡牌代码自动提取，例如 `20/C2·EN·10` → `C2`、`24A/P2·EN·7` → `P2`。每次 apply 同步刷新，兼容原 26 列和 27 列表格。仅处理 Promo 商品；同子类多代码组合只保留一个子类。无编号记录填“无编号”，无法确定时填“待核查”。

## LorcanaPlayer 浏览器取数与增量补录

GitHub Actions 的 **Check and update LorcanaPlayer** 接收浏览器导出的批次。沿用 `GOOGLE_SERVICE_ACCOUNT_JSON` 和 `SPREADSHEET_ID`。

### 自动找缺卡并批量取数

1. 在 **Run workflow** 选择 **mode → export_ids**，`batch_json` 留空，运行后打开 **Summary**。复制“表内页面 ID”下方的整段 JSON。也可以下载运行的 **lorcanaplayer-audit**，解压得到 `existing-ids.json`。该模式只读表格。
2. 第一次使用批量功能时，用最新版 [export_lorcanaplayer.js](export_lorcanaplayer.js) 替换 Edge/Chrome 中保存的片段代码。打开未筛选的 [卡牌目录](https://lorcanaplayer.com/cards/)，运行片段。
3. 在弹出的“LorcanaPlayer 批量核查”面板粘贴表内 ID，或选择 `existing-ids.json`。点 **检查缺卡并批量取数**。浏览器逐页比对 ID，然后只打开缺卡的详情数据。扫描期间保留目录标签页，面板显示进度和缺卡名单。
4. 点批次旁的 **复制到剪贴板**，回到 **Run workflow** 选择 **import_cards**，将输出粘贴到 `batch_json`。默认只预览，确认后勾选 `apply`。大批量会压缩，超过 GitHub 表单的容量时会自动拆分。每个拆分后的批次各提交一次。

网站拒绝请求或目录检查未完成时，面板会停止并提示。若详情采集途中停止，可先导入已经打包的卡牌，下一次用新导出的表内 ID 继续比对。批量核查扫描的只有网站目录，已收录卡牌的详情无需重取。

### 手动导出单张卡牌（可选）

1. 打开 LorcanaPlayer 的单张卡牌详情页，按 **F12** 打开开发人员工具。
2. 在 **源代码 / Sources → 代码段 / Snippets** 新建代码段，复制 [export_lorcanaplayer.js](export_lorcanaplayer.js) 的内容保存。只需设置一次。
3. 在同一个标签页依次打开要补的卡牌详情页，每次运行代码段。代码段把当前卡加入本批，并将整批 JSON 复制到剪贴板。重复采集同一页面 ID 会更新这一条。
4. 这一批提交后，在该标签页的开发人员工具 **控制台 / Console** 执行 `sessionStorage.removeItem('lorcanaplayer-import-v1')`，再开始下一批。关闭标签页前请先保存或提交剪贴板里的批次。

### 在 GitHub 检查并补录

1. 点 **Actions → Check and update LorcanaPlayer → Run workflow**，把剪贴板内容粘贴到 **batch_json**，保持 **apply** 关闭。
2. 在执行记录的 **Summary** 查看 `missing_cards`（待新增的编号、名称和系列）和 `existing_count`（已收录数）。
3. 确认后用同一批 JSON 新开一次运行，勾选 **apply**。脚本再次按页面 ID 和链接比对，只给 `洛卡纳Player` 明细新增缺失行。重复提交一批已补录的卡牌时，新增数为 0。

GitHub 单个批次输入最多 60 KB。上传内容只应包含卡牌数据，GitHub 表单不需要输入凭据。异画、普通与 Promo 均沿用网站页面 ID；内嵌图标保留为 `[Ink]`、`[Exert]` 等文本。来源页面没有的字段留空。

批次只覆盖提交的卡牌，Summary 的 `directory_updated` 为 `false`。`洛卡纳Player目录` 仍显示上一次完整核查的统计。需要完整统计时，原有的本地脚本 `sync_lorcanaplayer.py` 可以继续用完整实时目录做核对，前提是运行环境能访问网站。仅凭批次无法重建该目录。


## 目录变化颜色（两个 workflow 通用）

每次完整同步并成功更新目录后，脚本在自己的目录 A:D 设置原生条件格式：
- 浅绿色：本次新增的系列或 DLPC 子类。
- 浅黄色：与更新前相比，记录数、系列代码或备注改变的行（包含合计）。
- 下次更新重新比较；没有变化的行恢复原来的底色，不累计历史高亮。只检查、不写入时保留原状态。

按系列名称匹配，排序或插入行不会让其他系列误报。E:H 是脚本隐藏的上次值快照，请勿手工修改；遇到非脚本数据会停止，避免覆盖。只替换脚本自己的条件格式，保留其他规则。首次运行也会与已有目录比较；已被删除的行不保留在目录中。

更新 `zhucui1202` 仓库后，如果从 `Peppa-1202` fork 运行，请先 Sync fork → Update branch，再运行相应 workflow。
