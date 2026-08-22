# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

## Format

### User Instruction Entry
User instruction entries should follow this format:

[User Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Mentioned scenario or time]
- Instructions:
  - [Content of user teaching or instruction, described line by line]

### Project Knowledge Entry
Entries discovered by the Agent during task execution should follow this format:

[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]

## Deduplication Strategy
- Before adding a new entry, check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date information.
- This helps avoid redundant entries and keeps the memory file tidy.

## Entries

[Project Knowledge Summary]
- Date: 2026-08-21
- Context: Discovered by Agent while implementing ci-build-complete (patch_rtti.py / verify_symbols.py) and validating workflow YAML locally
- Category: Environment Configuration
- Instructions:
  - PyYAML 未预装，校验 workflow YAML 前先执行 `python3 -m pip install --break-system-packages pyyaml`
  - 环境无 pwsh，build-windows.ps1 只能人工审查或推送后由 CI 验证

[Project Knowledge Summary]
- Date: 2026-08-21
- Context: Discovered by Agent while testing patch_rtti.py idempotence/fail-closed and verify_symbols.py positive/negative cases
- Category: Testing Methods
- Instructions:
  - patch_rtti.py 本地测试：拷贝 node v24.x 的 common.gypi 到临时目录，分别以 linux/windows/macos 平台参数连跑两次验证幂等；删除 RTTI 标志的精简文件应使三个平台均报错退出（exit=1）
  - verify_symbols.py lws 用例：`gcc -fPIC -c` + ar 打包为正向；`gcc -fno-pic -fno-pie` 且引用全局数组的对象为负向（链接报 R_X86_64_* relocation）
  - verify_symbols.py node 用例：定义 v8::ValueSerializer::Delegate / ValueDeserializer::Delegate（带外部定义的虚析构）分别用 -frtti 与 -fno-rtti 编译打包；nm -C 断言 `typeinfo for ...` 两符号
  - 注意 `ar rcs` 对不同 basename 的成员是追加语义，复用归档文件做正反用例会互相污染，需每次重建干净归档
