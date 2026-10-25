# LPM 表项管理（TCAM + SRAM）

![LPM 查表流水线](docs/images/lookup-pipeline.svg)

设计细节见 [docs/architecture.md](docs/architecture.md)，验证方法见 [docs/test-method.md](docs/test-method.md)。

```bash
python tests/test_sweep.py
```

模型为行为级，不模拟时序。芯片规格、寄存器映射与编码格式属于受限信息，未公开。

MIT
