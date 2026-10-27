# Gold annotation format

The application evaluates only rows in the `test` split. It does not use `seed.jsonl`, which is synthetic and instructional.

Set `PAPERGRAPH_ANNOTATIONS_PATH` in `.env` to a JSONL file you create. Each non-empty line represents one document that is already in the local collection:

```json
{"document_id":"<local-document-uuid>","split":"test","entities":[{"text":"Named Method","type":"Method","start_offset":120,"end_offset":132}],"relations":[{"subject":"Named Method","predicate":"USES","object":"Named Dataset","evidence":"Named Method uses Named Dataset.","start_offset":120,"end_offset":153}]}
```

Offsets must refer to the normalized extracted document text shown in the document preview. Entity and relation scoring uses exact text, label, and span matching; report the matching rule when comparing another system. Keep train, development, and test documents separate before tuning the extractor. Use documents you have permission to annotate and evaluate.

The report includes entity and relation precision, recall, F1, and the share of stored evidence strings that match the corresponding extracted source offsets. It does not calculate entity-resolution clustering accuracy without cluster labels, and it does not treat this small project metric as a generalizable benchmark.
