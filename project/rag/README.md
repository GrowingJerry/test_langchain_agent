# 扩展预留说明

本目录预留与以下能力对接：

- **standard_kb**：GJB/Z 141、GJB 438C、GJB 5000B、GJB 9001C 等标准条款 RAG。
- **vector_search.py**：基于 Ollama embedding 或 Chroma / FAISS / Milvus 的语义检索。
- **docx_renderer.py**：读取 `document_placeholders` Sheet，配合 docxtpl 批量生成 Word。
- **report_generator.py**：测试报告汇总与封面、签署页等。
- **scenario_generator.py**：无人车、无人平台等场景的批量变体生成。

第一版主程序不依赖上述模块即可运行。
