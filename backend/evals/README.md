# Agent Business Eval

这个目录提供 Bobo Agent 的离线业务评测集和 replay harness。

目标：

- 让 Agent 评测从“零散 pytest 用例”升级为“版本化业务数据集 + 可批量回放 runner”
- 覆盖推荐、查询、graph path、memory context 等核心业务能力
- 作为 Prompt、Context、fast path、tool routing 调整时的回归基线

## 组成

- [cases/agent_business_eval_v1.json](/Users/serenl./bobo-agent/backend/evals/cases/agent_business_eval_v1.json)
  - 版本化业务评测集
- [agent_replay.py](/Users/serenl./bobo-agent/backend/evals/agent_replay.py)
  - 离线 deterministic replay runner
- [../scripts/run_agent_replay.py](/Users/serenl./bobo-agent/backend/scripts/run_agent_replay.py)
  - CLI 入口

## 运行

在 `backend/` 目录下：

```bash
./.venv/bin/python scripts/run_agent_replay.py
```

输出 Markdown scorecard：

```bash
./.venv/bin/python scripts/run_agent_replay.py --format markdown
```

输出 JSON：

```bash
./.venv/bin/python scripts/run_agent_replay.py --format json
```

指定评测集：

```bash
./.venv/bin/python scripts/run_agent_replay.py --suite evals/cases/agent_business_eval_v1.json
```

## 设计原则

采用当前较稳妥的离线 Agent 评测实践：

1. 业务 case 数据集版本化
2. runner deterministic，尽量避免外部网络和真实模型波动
3. 把“chat replay”和“memory context replay”分开建模
4. 输出结构化 scorecard，便于接 CI
5. case 以业务能力组织，而不是只以代码模块组织

## 后续扩展建议

优先新增这些 case 族：

- 记录饮品任务成功率
- 写库意图误判防线
- 品牌缺口诚实率
- memory extraction / retrieval 命中率
- 预算约束遵守率
- graph tool choice 正确率

