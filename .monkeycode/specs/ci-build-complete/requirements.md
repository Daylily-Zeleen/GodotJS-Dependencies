# Requirements Document

Feature: ci-build-complete

## Introduction

本系统是 GodotJS-Dependencies 的 CI 构建仓库，负责构建三个上游依赖库：

- **v8**：V8 monolith 静态库（来源 godotjs/GodotJS-Dependencies）
- **lws**：libwebsockets 静态库（来源 godotjs/GodotJS-Dependencies）
- **node**：Node.js 嵌入式静态库 libnode（来源 moluopro/libnode）

需求目标是让 node、v8、lws 三者都能分别构建通过；CI 只对修改相关内容触发对应的构建流程；最终通过一次完整构建进行最终确认。

当前仓库已有四个工作流（`build_v8.yml`、`build_lws.yml`、`build_node.yml`、`build_all.yml`），但均仅支持手动 `workflow_dispatch` / 复用的 `workflow_call`，缺少基于路径的自动触发；lws 的 Linux 构建未携带 `-fPIC`；libnode 构建在 `-fno-rtti` 下丢失 `v8::ValueSerializer::Delegate` 与 `v8::ValueDeserializer::Delegate` 的 RTTI 类型信息符号。

## Glossary

- **组件（component）**：v8、lws、node 三个依赖库之一。
- **触发路径（trigger paths）**：与某个组件强相关的仓库内文件路径集合，改动命中该集合时触发该组件的构建。
- **完整构建（full build）**：由 `build_all.yml` 一次性驱动 v8、lws、node 三个组件的全部平台组合并打包发布。
- **组件级构建（component build）**：由 `build_v8.yml` / `build_lws.yml` / `build_node.yml` 单独驱动某个组件的构建。
- **PIC（Position-Independent Code）**：位置无关代码，静态库在被链接进共享库时需要以 `-fPIC` 编译。
- **RTTI（Runtime Type Information）**：C++ 运行时类型信息，`typeinfo for ...` 符号由编译器在启用 RTTI 时生成。
- **Delegate 符号**：`v8::ValueSerializer::Delegate` 与 `v8::ValueDeserializer::Delegate` 两个类的 RTTI 类型信息符号（`typeinfo`），下游嵌入方派生这些类时需要它们。

## Requirements

### Requirement 1: 组件级路径触发

**User Story:** AS 依赖维护者，I want 只对修改的相关内容触发对应的组件 CI，so that 日常迭代时无需等待无关组件重新构建。

#### Acceptance Criteria

1. WHEN push 或 pull_request 事件到达且改动文件命中组件 v8 的触发路径，系统 SHALL 触发 `build_v8.yml`。
2. WHEN push 或 pull_request 事件到达且改动文件命中组件 lws 的触发路径，系统 SHALL 触发 `build_lws.yml`。
3. WHEN push 或 pull_request 事件到达且改动文件命中组件 node 的触发路径，系统 SHALL 触发 `build_node.yml`。
4. WHEN 改动文件未命中任何组件的触发路径，系统 SHALL 不触发对应组件的构建工作流。
5. 触发路径 SHALL 覆盖组件专属的 workflow 文件、composite action 目录、构建脚本目录与配置目录。

### Requirement 2: 完整构建确认

**User Story:** AS 依赖维护者，I want 在所有组件分别通过后能够手动发起一次完整构建，so that 合并发布前可整体确认。

#### Acceptance Criteria

1. WHEN 维护者手动运行 `workflow_dispatch`，系统 SHALL 执行 `build_all.yml`。
2. `build_all.yml` 运行期间，系统 SHALL 依次（或并行）构建 v8、lws、node 三个组件的全部平台组合。
3. 完整构建中的任一组件失败时，系统 SHALL 将该组件构建标记为失败，且整体发布流程 SHALL 不执行。
4. 完整构建通过后，系统 SHALL 打包并发布包含三个组件产物的 Release。

### Requirement 3: lws Linux 构建带 -fPIC

**User Story:** AS 依赖维护者，I want lws 的 Linux 静态库以 `-fPIC` 编译，so that 下游 GodotJS 嵌入时能将其链接进共享库。

#### Acceptance Criteria

1. WHEN 组件 lws 在 Linux（x86_64 或 arm64）平台构建，系统 SHALL 以 `-fPIC` 编译 libwebsockets 静态库。
2. 构建结束后，系统 SHALL 校验 `libwebsockets.a` 的对象文件全部为位置无关代码。
3. IF 校验发现非 PIC 的对象文件，系统 SHALL 判定该组件构建失败。
4. Windows / macOS / Android / iOS 平台的 lws 构建行为 SHALL 保持现状不变。

### Requirement 4: libnode 保留 Delegate RTTI 符号

**User Story:** AS 依赖维护者，I want `v8::ValueSerializer::Delegate` 与 `v8::ValueDeserializer::Delegate` 的 RTTI 类型信息被编入 libnode 静态库，so that 下游嵌入方继承这两个类时不产生链接错误。

#### Acceptance Criteria

1. 构建 libnode 期间，系统 SHALL 使 V8 相关目标启用 RTTI（避免在 V8 目标上使用 `-fno-rtti`）。
2. 构建结束后，系统 SHALL 校验 `libnode.a`（或 Windows 的 `libnode.lib`）中存在 `typeinfo for v8::ValueSerializer::Delegate` 与 `typeinfo for v8::ValueDeserializer::Delegate` 两个符号。
3. IF 上述任一符号缺失，系统 SHALL 判定该组件构建失败。
4. 该校验规则 SHALL 至少覆盖 Linux 与 macOS 平台；Windows 平台若工具链无 `nm`，SHALL 采用等效的符号校验手段。

### Requirement 5: 构建产物整体校验

**User Story:** AS 依赖维护者，I want 每个组件构建通过后校验其产物结构与关键符号，so that 发布前的静态库满足下游链接要求。

#### Acceptance Criteria

1. 每个组件构建结束后，系统 SHALL 运行产物校验脚本，校验平台目录结构、必需头文件与静态库文件存在。
2. 组件 lws 在 Linux 构建结束后，系统 SHALL 额外执行 `-fPIC` 校验。
3. 组件 node 构建结束后，系统 SHALL 额外执行 Delegate RTTI 符号校验。
4. IF 任何校验失败，系统 SHALL 判定该组件构建失败并停止后续发布流程。
