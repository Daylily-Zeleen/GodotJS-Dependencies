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
- [x] `build_all` 已改为使用严格期望集合，并在发布前强制要求 LWS/V8/Node 三个 staging 根目录存在。
- [x] `apply_icu_profile.sh` 已严格要求恰好移除一个 `--delete-tmp`，并使用兼容 macOS/BSD 的 `sed -i.bak`。
- [x] ICU 数据验证检查全部 19 个 selected locale、拒绝其它 `lang/*.res`，并允许禁用 delete-tmp 后预期共存的 `icutmp/icudt*.dat` 与 `icutmp/icusmdt*.dat`。
- [x] 统一 LWS/V8/Node workflow 的 Windows 架构输入别名。
- [x] Windows 构建固定执行 Python setup，校验 canonical ICU profile，并使用无 BOM UTF-8 写回 gyp 文件。
- [x] LWS/V8/Node reusable workflow setup 现在拒绝组件不支持的平台和无效架构。
- [x] standalone Python 全部编译通过；三个 workflow 内嵌 setup Python 代码也已提取并编译通过。
- [x] ICU profile 解析通过，矩阵有效输入通过，`linux-nope`、无 SDK OHOS 等无效输入按预期失败。
- [x] 所有 Node Shell 脚本通过 `bash -n`，`git diff --check` 通过。
- [x] 推送前审查发现并修复：裸未知平台/被 skip 或无 SDK 的显式平台可能导致空或部分矩阵；LWS Windows Python setup 不能依赖未验证的主机环境。
- [x] 修复 Node release 二次校验在删除 CI-only `node-commit.txt` 后仍强制要求 commit marker、导致发布必然失败的问题。
- [x] 修复显式混合平台请求被 `SKIP_APPLE`/`SKIP_WINDOWS` 静默删减、可能造成部分矩阵假通过的问题。
- [ ] 将本轮 CI 修复提交并推送到 fork 的 `main`。
- [ ] 监控包含本轮提交的 GitHub Actions run，读取失败日志并继续修复。

## 已确认风险

1. 当前远端唯一最近成功 run `31390479332` 早于本轮未提交修改，因此不能作为本轮 CI 验证。
2. Node small-ICU 数据校验依赖构建保留的 `icusmdt*.dat` 或 `icutmp/icudt*.dat`；如果 Node 上游改变生成路径，脚本会 fail closed。
3. YAML 专用 lint 工具当前未安装；当前依赖嵌入 Python 编译、Shell/Python 检查和 GitHub Actions 实际解析。

## 最近验证

- Python 3.13.7：全部独立脚本编译通过。
- 三个 workflow setup heredoc：提取并编译通过。
- ICU profile：19 locale JSON 校验通过。
- `expected_platforms.py`：有效别名通过，未知架构、无 SDK OHOS、被 skip 平台均按预期失败。
- `bash -n scripts/node/*.sh`: 通过。
- `git diff --check`: 通过。
- GitHub Actions：当前无进行中 run；没有包含本轮修改的远端 run，未虚构 CI 验证结果。
