# Checked-in fixture files

Every other fixture in this suite is generated at test time by
`tests/fixtures/builders.py` and `tests/fixtures/corpus.py`. The files here are the
exceptions: formats no Python library can write.

| File | What it is | How it was made |
|------|------------|-----------------|
| `legacy_birth.doc` | Binary Word 97-2003 document (OLE2 container) holding the `birth_lahore` sample as a two-column table | `build_docx(path, "birth_lahore", as_table=True)`, opened in Microsoft Word and saved with *Save As → Word 97-2003 Document* (`wdFormatDocument97`) |

To regenerate `legacy_birth.doc`, build the .docx as above and save it as Word
97-2003 from Word, or from LibreOffice:
`soffice --headless --convert-to doc:"MS Word 97" birth.docx`.
