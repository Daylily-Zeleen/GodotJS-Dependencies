# CI 修复任务状态

更新时间：2026-08-11

## 目标与约束

- 持续监控 GitHub Actions，读取失败 job/step 日志并按根因修复。
- LWS、V8、Node 分别验证发布产物；全量构建必须验证所有实际应构建的平台。
- Node 使用公开 `nodejs/node` 源码，复刻 `moluopro/libnode` 可公开确认的 `selected-locales-full-break-v1` ICU profile 和 small-icu 构建方式。
- artifact 保留期为 1 天；清理旧的无用 workflow、run 和缓存时不得影响当前有效构建。
- 禁止通过“缺平台即忽略”的逻辑制造 CI 假通过；每次修复后执行 YAML、Shell、Python、diff 检查。
- 每次任务开始、发现问题、修复和验证结果都同步到本文件。

## 当前进度

- [x] 查询 GitHub Actions：当前没有进行中的 run，也没有新的失败 run；最近一次 Build Node run `31390479332` 成功。
- [x] 创建并持续更新 `TASK_STATUS.md`。
- [x] 确认 `retention-days: 1` 和 Node `timeout-minutes: 300` 仍存在。
- [x] 完成公开 ICU profile、small-icu 参数和 profile 内容校验接入。
- [x] 新增 `scripts/expected_platforms.py`，按 LWS/V8/Node 独立矩阵、用户选择、跳过 Apple/Windows 和 OHOS 可用性生成严格期望集合。
- [x] 修复 `expected_platforms.py` 无效平台分支的 NameError，并验证 fail-closed 错误路径。
- [x] `build_all` 已改为按组件分流平台输入，并在发布前按实际启用组件强制验证 staging 根目录和严格平台集合。
- [x] `apply_icu_profile.sh` 已修复为严格要求并移除 Node v22.x-v24.x 中两个 `--delete-tmp` action。
- [x] ICU 数据验证检查全部 19 个 selected locale、拒绝其它 `lang/*.res`，并允许禁用 delete-tmp 后预期共存的 `icutmp/icudt*.dat` 与 `icutmp/icusmdt*.dat`。
- [x] 统一 LWS/V8/Node workflow 的 Windows 架构输入别名。
- [x] Windows 构建固定执行 Python setup，校验 canonical ICU profile，并使用无 BOM UTF-8 写回 gyp 文件。
- [x] LWS/V8/Node reusable workflow setup 现在拒绝组件不支持的平台和无效架构。
- [x] standalone Python 全部编译通过；四个 workflow 内嵌 setup Python 代码也已提取并编译通过。
- [x] ICU profile 解析通过，矩阵有效输入通过，`linux-nope`、无 SDK OHOS 等无效输入按预期失败。
- [x] 所有 Node Shell 脚本通过 `bash -n`，YAML 结构解析通过，`git diff --check` 通过。
- [x] 修复 Node release 二次校验在删除 CI-only `node-commit.txt` 后仍强制要求 commit marker、导致发布必然失败的问题。
- [x] 修复显式混合平台请求被 `SKIP_APPLE`/`SKIP_WINDOWS` 静默删减、可能造成部分矩阵假通过的问题。
- [x] 将本轮 CI 修复提交并推送到 fork 的 `main`：首个提交 `cbeedd698059c8693af637c7ce1597803693be8b`，后续 ICU patch 提交 `685106808b42da39839a8e374f13ec187eb9f609`、配置解析提交 `fef3a6720599b13b660eb90260bbb37f9dd5abc8`、配置路径提交 `6cd34081338cf4338882a2839cef97be219804f5`。
- [x] LWS Linux smoke run `31462169259` 成功。
- [x] V8 Linux smoke run `31462172647` 成功。
- [x] Node 两个 `--delete-tmp` action 已按真实 Node v22.x-v24.x 源码处理。
- [x] Node run `31462904547` 越过 ICU trim patch，但旧验证器无法解析生成的 `icu_config.gypi` 表示。
- [x] Node run `31463829269` 使用 AST 验证器重跑，仍报告 `icu_small` 未启用；已确认验证器一直读取了错误的 `icu_config.gypi`。
- [x] 修复所有 Node 平台脚本：从正确的 `config.gypi` 读取 `icu_small`/`icu_locales`，而不是 ICU 工具配置 `icu_config.gypi`。
- [x] 定位 Node run `31464175446` 的新失败：GYP 预创建空 `icutmp`，而 `icutrim.py` 拒绝已有目录；改为只允许空目录、仍拒绝非空 stale 目录。
- [x] 加固 Windows ICU tmpdir patch：规范化 CRLF/LF，并断言旧 guard 已完全移除。
- [x] 提交并推送 ICU tmpdir 修复（`cc3a44160e1f1f7263d1b2d60a418633d8c166c1`）；Node retry 4 `31465087114` 越过 config/icutrim 全部历史失败点，make 构建成功。
- [x] 定位 Node retry 4 失败：make 成功后，`verify_icu_data.py` 对 `icutmp/icusmdt78.dat` 执行 `icupkg -l`——该文件只是裁剪档案的字节副本，item 前缀仍为 `icudt78l/`，与文件名 `icusmdt78` 不匹配，icupkg 拒绝列出。Node 的 genccode action 是 `cp` 后直接嵌入（`-e` 仅命名 C 入口），从不重命名 item 前缀。
- [x] 修复 `verify_icu_data.py`：改为对裁剪后的 `icutmp/icudt*.dat`（前缀与文件名一致）做 `icupkg -l` 内容验证，并逐字节比对 `icusmdt*.dat` 副本必须与裁剪档案一致；Windows 无副本时仅验证裁剪档案。
- [x] 修复 `verify_icu_data.py` 对 `lang/res_index.res` 的误报：icutrim 会对每个被裁剪的资源树重建 `res_index.res`（hasIndex），它是树索引而非 locale，已从 unselected-locale 检查中排除。
- [ ] 提交并推送 verify_icu_data.py 修复，然后复跑 Node smoke。

## 已确认风险

1. Node v22.x-v24.x 公开 GYP 有两个平台特定 `--delete-tmp` action，已按实际源码严格处理。
2. Node 的 configure 参数、`config.gypi` 验证路径、空 icutmp 兼容均已在 retry 4 通过；当前唯一未解问题是验证器应列出裁剪档案 `icutmp/icudt*.dat` 而非 `icusmdt*.dat` 副本（item 前缀不匹配），已修复待复跑确认。
3. Node small-ICU 数据校验依赖构建保留的 `icutmp/icudt*.dat` 裁剪档案，并要求 `icusmdt*.dat` 副本与其逐字节一致；如果 Node 上游改变生成路径，脚本会 fail closed。
4. YAML 专用 lint 工具当前未安装；本地已用 PyYAML 做结构检查，并依赖 GitHub Actions 实际解析。

## 最近验证

- Python 3.13.7：全部独立脚本编译通过。
- 四个 workflow setup heredoc：提取并编译通过。
- ICU profile：19 locale JSON 校验通过。
- `expected_platforms.py`：有效别名通过，未知架构、无 SDK OHOS、被 skip 平台均按预期失败。
- `bash -n scripts/node/*.sh`: 通过。
- PyYAML workflow/action 结构解析：通过。
- `git diff --check`: 通过。
- GitHub Actions：LWS smoke `31462169259` 成功；V8 smoke `31462172647` 成功；Node retry 4 `31465087114` 在 make 成功后因验证器错误列出 `icusmdt78.dat`（前缀不匹配）而失败，已修复验证器并排除 res_index 误报，待复跑。
- `verify_icu_data.py` fixture：9 个用例通过（POSIX/Windows 布局、副本一致、缺失/多余 locale、res_index 排除、icupkg 失败、无裁剪档案、stale dat 均按预期 fail-closed）。
