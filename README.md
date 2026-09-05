# Furniture Cost Calculator - Step 1

This first version only proves that the pricing engine works.

## Files

- `main.py` - starts the program and compares Python with the Excel sample.
- `calculator.py` - all price/markup calculations.
- `excel_reader.py` - reads sample project data from the Excel file.
- `manual_test.py` - very small calculation test without Excel.
- `requirements.txt` - Python dependency list.
- `data/Samata 1pvz.xlsx` - sample Excel workbook.

## Run locally in VS Code

1. Install Python 3.11+.
2. Open this whole folder in VS Code: `File -> Open Folder`.
3. Open VS Code terminal: `Terminal -> New Terminal`.
4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Run:

```bash
python main.py
```

Expected final line:

```text
MATCH: Python calculation matches Excel.
```

## Important

This is only Step 1. Do not add GPT or Streamlit yet. First make the calculation engine reliable against the real Excel files.
