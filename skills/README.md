# skills/ — 技能系统（Day9）

## Skill 与 Tool 的区别

| | Tool | Skill |
|---|------|-------|
| 粒度 | 单次函数调用 | 领域知识 + 多步工作流 |
| 调用方式 | API function-calling | 系统提示词注入 |
| 示例 | `read("a.txt")` | "收到教师评价查询时，先 search → 归纳关键信息 → 用 @引用@ 标注来源（后端校验后自动去除）" |

## 架构

```
skills/loader.py ──► 扫描 */SKILL.md ──► 解析 frontmatter ──► 注入系统提示词
                                            │
                                            ├─ name: 技能名
                                            ├─ description: 召回条件
                                            └─ body: 执行流程（Markdown）
```

## 已注册的 Skills

### 1. example-skill
生成 CSV 格式报告的示例 Skill，展示 Skill 系统的基本用法。

### 2. teacher-eval-search（教师评价搜索）

**领域**：高校教师评价查询与对比

**流程**：
1. 接收教师评价查询
2. 调用 `teacher_search` / `course_search` 工具获取数据
3. 按 SKILL.md 流程归纳总结（教学风格、作业量、考试难度、给分情况）
4. 用 `@序号+关键词@` 格式引用评价原文
5. 引用安全校验（safety.py 后处理）

**组件**：
- `search_engine.py`：CSV 数据索引（230k+ 条评论，单例模式）
- `safety.py`：引用校验（序号存在性 + 关键词匹配 + 未标注声明检测）

## 添加新 Skill

1. 在 `skills/` 下新建目录（如 `my-domain/`）
2. 创建 `SKILL.md`：
   ```markdown
   ---
   name: my-domain
   description: 何时该用这个 skill
   ---
   ## 步骤
   1. ...
   2. ...
   ```
3. 可选：添加 Python 脚本作为该 skill 的支撑代码
4. 重启 agent 即可自动加载
