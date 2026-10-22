# Chisels Play Cost Calculator

AI-assisted furniture cost estimation and quotation tool for Chisels Play.

The application reads a furniture drawing (PDF or image), extracts priceable components, matches them against the company price catalog, calculates an internal selling estimate using category coefficients and a project multiplier, helps the estimator resolve uncertain or unmatched items, and generates a client-ready PDF quotation.

> **Current status:** internal estimator/testing application. The workflow is designed so it can later be separated into internal/admin/client interfaces.

---

## 1. Main workflow

```text
Upload drawing
    ↓
AI extracts furniture + BOM-level components
    ↓
AI/catalog matching
    ↓
Estimator reviews matches and exceptions
    ↓
NO MATCH items can use:
- existing catalog search
- market-price search
- custom item
- optional save-to-catalog for future estimates
    ↓
Needs Review summary
    ↓
Internal price calculation
    ↓
Estimator chooses final client price
    ↓
Quotation preview + warning-only validation
    ↓
Generate client PDF quotation
```

The application deliberately separates the **internal calculated selling price** from the **final client price**. The estimator may adjust the final client price without changing the underlying calculation.

---

## 2. Main features

### AI drawing extraction

The application accepts furniture drawings and extracts information such as:

- furniture name and type;
- overall width, height and depth;
- doors and shelves;
- materials;
- BOM-level priceable parts;
- component type;
- quantity;
- length, width, height and thickness when available;
- drawing/source context;
- AI confidence;
- uncertain information that should be reviewed.

The extractor is intentionally instructed not to invent missing dimensions or quantities.

### Deterministic hinge calculation

For confirmed hinged doors, hinge quantity is calculated in Python rather than guessed by AI.

Current rule per hinged door:

| Door height | Hinges |
| --- | ---: |
| up to 900 mm | 2 |
| 901-1600 mm | 3 |
| 1601-2100 mm | 4 |
| above 2100 mm | 5 |

Example: a 2200 mm hinged door receives **5 hinges**.

Important behavior:

- sliding doors are never counted as hinged doors;
- if the opening system is unclear, hinges are not invented;
- if the real door-leaf height is unavailable, automatic hinge quantity is not calculated;
- if hinges are explicitly present in the BOM/drawing, the system keeps the explicit hinge line and does not add a duplicate derived hinge line.

### Catalog matching

Extracted components are matched against the company catalog.

The matching workflow considers information such as:

- component name;
- material;
- component type;
- unit;
- brand/model when available;
- thickness and dimensional context;
- previous human matching feedback.

An estimator can still change the selected catalog item manually.

### Learned corrections

Human catalog choices can be stored as matching feedback so future similar parts can use previous decisions.

The project supports local feedback storage and can use Supabase when configured. For deployed applications, persistent database storage is preferred over relying on Streamlit's local filesystem.

Learned corrections are reviewed on the **Admin** page (see "Admin page" below), where wrong ones can be deleted.

### Market price check

The estimator can run a market-price comparison for a component.

The market-price feature is intended as a **reference/check**, not as an automatic replacement for the internal catalog.

It can:

- search relevant Lithuanian market offers;
- normalize usable offers;
- compare market results to the selected catalog price;
- account for units and panel thickness where possible;
- show average/minimum/maximum prices;
- show individual offers and sources;
- prefer exact matches when available;
- flag suspicious/unusual offers;
- allow a market price to be used for the current estimate when appropriate.

Market results are **never automatically added to the catalog**. For an unmatched component, the estimator can choose a specific real market offer, tick **Add to catalog** for the offers worth keeping, and then click **Send ticked offers for catalog approval**. Nothing is saved until that button is clicked, so changing the category or other choices beforehand does not create extra catalog items. The actual selected offer is saved; the market average is not saved as a catalog product.

Every **Check market prices** / **Search again** starts with a fresh offers table. Ticks from a previous search are cleared, so a tick can never move to a different offer when the new search returns offers in another order.

### Better NO MATCH workflow

When no suitable catalog item exists, the UI stays intentionally simple. The estimator sees only two explicit resolution actions:

1. **Check market price** - search current market offers and select one real offer to use.
2. **Create custom item** - manually enter the item name, category, unit and unit price.

If neither action is chosen, the part simply remains unresolved and is not included in the estimate. There is no separate Ignore or Leave unresolved button.

For market results, each real offer can be selected for the estimate (**Use**) and can independently be ticked **Add to catalog**; ticked offers are sent for admin approval with the **Send ticked offers for catalog approval** button. The market average remains reference information only.

When a new drawing is analysed or **Clear product** is clicked, all NO MATCH choices, custom-item fields and market selections from the previous drawing are cleared, so they can never carry over into a new estimate.

### Custom items and catalog additions

A custom item can be created when the component is valid for the current project but no suitable catalog item exists.

Fields include:

- name;
- category;
- unit;
- unit price.

A custom item always works in the current estimate. To keep it for future estimates, the estimator clicks **Send to catalog for approval**. The item is saved once, with the name, category, unit and price shown at the moment of clicking; typing in the fields does not save anything.

Items sent by estimators (custom items and market offers) are saved as **pending** (`approved = false`). A pending item is not used in future estimates until an admin approves it on the Admin page. The part's name, material type and component type are saved with it automatically, and market offers also keep the supplier, product link, thickness and whether the offer was an exact or similar match (in notes).

Saved catalog additions are stored:

- in the Supabase table `catalog_items` when `SUPABASE_URL` and `SUPABASE_KEY` are configured (see section 7);
- otherwise in the local file:

```text
data/catalog_additions.json
```

If Supabase is configured but temporarily unavailable, the item is saved to the local file instead.

Duplicate protection compares name, category and unit while ignoring upper/lower case, spacing, punctuation and Lithuanian letters (for example `Lukštas ąžuolo` and `Lukstas azuolo` count as the same item). Sending the same item twice shows **Already in catalog** or **Already waiting for admin approval** instead of creating a copy. An item an admin disabled is not added again.

The local file is protected against data loss:

- an empty file is treated as "no additions yet";
- a damaged file is kept as a backup (`catalog_additions.unreadable-<date>.json`) instead of being overwritten;
- if the file cannot be opened at that moment, saving is refused rather than replacing the existing items;
- writes go through a temporary file and are safe when several estimators save at the same time.

The original `data/prices.xlsx` workbook is intentionally not rewritten from the Streamlit UI. On every rerun the application merges approved additions with the base catalog. This reduces the risk of corrupting the pricing workbook.

> On Streamlit Cloud, local filesystem writes are not durable across redeploys/restarts. Configure Supabase so catalog additions and learned corrections are kept permanently.

### Admin page

The **Admin** page appears in the sidebar. It is protected by the `ADMIN_PASSWORD` secret; without that secret the page stays locked. The admin enters their name in the sidebar, and it is recorded as `approved_by` when an item is approved.

**Setup (shown until done):**

- **Import price list from prices.xlsx** - copies every row of `data/prices.xlsx` (with material type, component type, brand/model, thickness, keywords, synonyms, description, allowed use, needs-review flag and notes) into the catalog store, marked as approved price-list items. Repeated rows (for example `Kabykla` / `kabykla`) are imported once. From then on the catalog store is the **live catalog** and `prices.xlsx` is only a backup; category coefficients and the project multiplier are still read from `prices.xlsx`.
- **Copy learned corrections to Supabase** - copies corrections that exist only in `data/match_feedback.json`. Corrections already in Supabase are never overwritten. Once Supabase is configured, learned corrections are read only from Supabase, so a deleted correction cannot come back from the file in the repository.

Tabs:

- **Pending approval** - items sent by estimators. The admin can correct any field and then **Approve**, or **Reject and delete**.
- **Catalog** - every approved item (price list and items added in the app): search, filter by source and status, **Add item** (added as approved), edit any field, disable/enable and delete (with confirmation). Disabled items are kept but not used in estimates.
- **Coefficients** - the company category coefficients (for example LMDP ×3, METAL ×5) and the default project multiplier (×1.4). The admin can change values, add a category and remove a category that no catalog item uses. Until they are saved here they are read from `prices.xlsx`; after saving they are stored in the database (`pricing_settings` table) and used by every new estimate.
- **Import / export Excel** - **Download catalog as Excel** creates a file in the same format as `prices.xlsx` (All_Items, Categories, Global_Settings). **Import** reads an uploaded Excel file, shows how many rows are new, changed or invalid before anything is saved, then adds new items and updates changed ones (matched by name, category and unit). Import never deletes items. If the uploaded file also has Categories and Global_Settings sheets (like `prices.xlsx`), the admin sees which coefficients differ and can replace them in the same import, so the whole price file can be managed from the Admin page.
- **Learned corrections** - the saved matching corrections, with CSV download and deletion of wrong ones.

Adding, approving and importing require the admin's name in the sidebar; it is saved as `approved_by`.

Learned corrections point to catalog items by name, category and unit. When an admin renames an item or changes its category or unit, its corrections are moved to the new name automatically (votes are kept, and merged if the same part already pointed to the new name). The edit area shows how many corrections use an item, and deleting such an item shows a warning, because its corrections are then ignored.

In the Calculator, the box **Category coefficients for this estimate** starts from the company coefficients. An estimator can change them for the current estimate only (the totals, breakdown and quotation follow); **Reset to company coefficients**, analysing a new drawing or **Clear product** brings back the company values. The global multiplier also starts from the admin's default and can be changed per estimate.

After 5 wrong admin passwords the sign-in is locked for 5 minutes.

The sidebar shows **Calculator** and **Admin** links; Streamlit's automatic page list is turned off in `.streamlit/config.toml` because it would label the main page "app".

Supabase is read page by page (1,000 rows per request), so catalogs and learned corrections larger than 1,000 rows are loaded completely. If Supabase cannot be reached, the Calculator shows a warning and falls back to `prices.xlsx`, and sending an item for approval shows "nothing was saved, please try again" instead of saving it to a temporary file.

Approved changes are used by the estimator page on its next rerun (the estimator page keeps the catalog for at most 5 minutes; every admin change refreshes it immediately).

The AI matcher receives each catalog item's material type, component type, brand/model, thickness, keywords and synonyms in addition to name, category, unit and price, so Lithuanian and English names of the same item are recognised.

When sending an item for approval, the estimator can optionally add a brand/model (custom items) and notes for the admin.

### Needs Review

The application collects potentially problematic components into a dedicated **Needs review** section.

Current warning conditions include:

- unresolved/no-price component;
- zero quantity;
- missing pricing coefficient;
- low-confidence AI catalog match (below 70%);
- catalog price differing substantially from a checked market average (35% or more).

These warnings are intended to help the estimator focus on exceptions instead of rechecking every normal item.

They currently **do not block testing or quotation generation**.

### Simplified estimator UI

The manual **Drawing details** section has been removed. Extracted drawing information can still be kept internally for matching and quotation defaults, but the estimator no longer has to work through a separate width/height/depth/doors/shelves form before pricing.

### Calculation breakdown

The internal calculation breakdown shows values such as:

- quantity;
- unit;
- unit price;
- raw cost;
- category;
- category coefficient;
- value after the category coefficient;
- subtotal;
- global project multiplier;
- calculated selling price.

This is internal estimator information and should not appear in the client quotation.

### Editable final client price

The final client price is intentionally separate from the deterministic calculator result.

Example:

```text
Calculated selling price: €4,185.00
Final client price:       €4,500.00
```

This allows the estimator to make a commercial adjustment based on the market, project complexity or another business decision without changing the underlying calculation.

### Quotation preview

Before generating the final PDF, the app displays a quotation preview containing:

- quote number;
- quote date;
- validity date;
- client name;
- product/furniture name;
- furniture price;
- additional services;
- discount;
- VAT;
- final total.

The preview and PDF use the same quote-total calculation function to reduce inconsistencies.

### Ready-to-quote check

The application performs a final warning-only check before PDF generation.

Examples:

- unresolved component warning;
- missing client name;
- missing quote number;
- missing furniture/product name;
- final client price is zero;
- validity date earlier than quote date.

**Important:** during the current testing stage, these warnings do not disable the PDF-generation button.

### Client PDF quotation

The app generates a client-facing PDF directly with ReportLab.

The quotation includes:

- Chisels Play logo from `data/logo.jpg`;
- title `KOMERCINIS PASIŪLYMAS`;
- quote number;
- date and validity date in `DD/MM/YYYY` format;
- fixed supplier details;
- client name;
- furniture/product row;
- optional service rows;
- VAT;
- optional discount;
- final total;
- company quotation notes/conditions.

Current fixed supplier details:

```text
IĮ "Buki kaltai"
305189636
LT100012484610
Šilo g. 6, Lekėčiai, Šakių r.
062533752
```

The quotation deliberately contains client-facing prices only. Internal material costs, matching confidence, coefficients and market-search research are not included.

Client names, product names, service names and the quote number are printed exactly as typed, including characters such as `&`, `<` and `>`.

Lithuanian letters (ą, č, ę, ė, į, š, ų, ū, ž) need a Unicode font. The PDF uses DejaVu Serif, Liberation Serif or Times New Roman when one is installed. On Streamlit Cloud, `packages.txt` installs `fonts-dejavu-core` so the font is always available; without it the PDF would fall back to a basic font and show these letters as empty boxes.

The quote PDF is cleared when a new drawing is analysed or **Clear product** is clicked, so a previous client's quotation cannot be downloaded by mistake.

---

## 3. Pricing logic

The estimator follows the existing category-coefficient model.

Typical category coefficients currently used by the project are:

| Category | Coefficient |
| --- | ---: |
| metal | 5.0x |
| LMDP | 3.0x |
| PASLAUGA | 1.8x |
| MASYVAS | 1.1x |
| FURNITURA | 2.0x |
| STIKLAS | 2.0x |
| BE KOFO | 1.0x |

The live values are read from the pricing workbook, so the workbook remains the source for the actual configured coefficients.

Conceptually:

```text
raw line cost = quantity × unit price
line selling value = raw line cost × category coefficient
project calculated price = line subtotal × global project multiplier
```

The final client price can then be adjusted manually by the estimator.

---

## 4. Quotation calculation

The client quotation calculation is separate from the internal component calculation.

The quotation starts with the estimator's **final client furniture price**, then adds optional service rows such as:

- measurement;
- delivery;
- installation;
- other services.

Conceptually:

```text
subtotal = final furniture price + service rows
subtotal after discount = subtotal - discount
VAT = subtotal after discount × VAT %
final total = subtotal after discount + VAT
```

Discount can be entered as an amount or percentage.

The PDF hides the discount row when no meaningful discount is applied.

---

## 5. Project structure

Typical project structure:

```text
chisels_play/
├── app.py
├── calculator.py
├── catalog_store.py
├── component_matcher.py
├── feedback_store.py
├── market_prices.py
├── pdf_extractor.py
├── price_reader.py
├── quote_generator.py
├── requirements.txt
├── packages.txt             # system packages for Streamlit Cloud (PDF font)
├── supabase_schema.sql      # Supabase tables (run once in the SQL editor)
├── pages/
│   └── Admin.py             # admin page: approvals, catalog, learned corrections
├── README.md
├── .env                     # local only - never commit
├── .gitignore
└── data/
    ├── logo.jpg
    ├── prices.xlsx
    ├── catalog_additions.json  # catalog additions when Supabase is not configured
    └── match_feedback.json     # local fallback when used
```

### Important files

#### `app.py`
Main Streamlit interface and estimator workflow.

Responsible for:

- upload UI;
- AI analysis flow;
- catalog selection;
- market-price UI;
- NO MATCH handling;
- custom items and optional catalog saving;
- Needs Review;
- internal calculation display;
- final client price;
- quotation preview;
- warning-only quote validation;
- PDF download;
- learned-correction management UI.

#### `catalog_store.py`
Stores custom items and selected market offers sent by estimators in Supabase (table `catalog_items`) when configured, otherwise in `data/catalog_additions.json`. New items are pending until approved; only approved, active items are used in estimates. Also provides the approve, edit, disable and delete operations used by the Admin page. Prevents duplicates, protects the local file against data loss, and keeps catalog persistence separate from the UI.

#### `pages/Admin.py`
Password-protected admin page: pending approvals, catalog additions and learned corrections.

#### `supabase_schema.sql`
Creates the `catalog_items`, `match_feedback` and `pricing_settings` tables in Supabase.

#### `packages.txt`
System packages installed by Streamlit Cloud. Currently `fonts-dejavu-core`, needed for Lithuanian letters in the quotation PDF.

#### `pdf_extractor.py`
Extracts furniture/component information from uploaded PDFs/images using OpenAI and applies deterministic hinge post-processing.

#### `component_matcher.py`
Matches extracted parts against the price catalog (including each item's material type, component type, brand/model, thickness, keywords and synonyms) and calculates quantities based on unit/component information.

#### `calculator.py`
Performs the deterministic project-price calculation.

#### `price_reader.py`
Reads the company Excel pricing workbook. Columns of the All_Items sheet are read by their header names, so the extra item details (material type, brand/model, keywords, synonyms and so on) are used, and the same reader is used for Excel imports on the Admin page.

#### `market_prices.py`
Performs external market-price checks and normalizes/comparers offers for estimator reference.

#### `feedback_store.py`
Stores learned catalog-matching corrections locally or through configured persistent storage.

#### `quote_generator.py`
Creates the client-ready PDF quotation and contains the quotation total calculation.

#### `data/prices.xlsx`
Company price catalog and pricing configuration.

#### `data/logo.jpg`
Logo used in the quotation PDF.

---

## 6. Installation

### Requirements

Recommended:

- Python 3.10+
- pip
- OpenAI API key

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Current Python dependencies:

```text
streamlit
openai
openpyxl
python-dotenv
requests
reportlab
Pillow
```

---

## 7. Environment configuration

Create a local `.env` file in the project root:

```text
OPENAI_API_KEY=your_key_here
```

Do not commit `.env` to Git.

Recommended `.gitignore` entries:

```text
.env
__pycache__/
*.pyc
```

If persistent learned corrections are configured through Supabase, keep those credentials in environment variables/secrets as well rather than source code.

### Persistent storage (Supabase) and admin password

1. Create a project at supabase.com.
2. Open **SQL Editor**, paste the contents of `supabase_schema.sql` and click **Run**. This creates the tables `catalog_items`, `match_feedback` and `pricing_settings` and turns on Row Level Security, so the tables cannot be read or changed from outside with the public key.
3. Open **Project Settings → API Keys** and copy the project URL and a **secret** key (`sb_secret_...`). The legacy `service_role` key also works. Never use the secret key in browser code and never commit it.
4. Add these to the Streamlit secrets (or environment variables):

```text
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_KEY = "sb_secret_..."
ADMIN_PASSWORD = "choose-a-strong-password"
```

When `SUPABASE_URL` and `SUPABASE_KEY` are set, learned corrections and catalog additions are stored in Supabase and survive restarts and redeploys. Catalog rows with `active = false` or `approved = false` are not used in estimates.

`catalog_items` columns: `id`, `name`, `category`, `unit`, `price`, `source_type`, `source_url`, `material_type`, `component_type`, `thickness_mm`, `brand_model`, `added_for_part`, `active`, `created_at`, `updated_at`, `supplier`, `created_by`, `approved`, `approved_by`, `approved_at`, `notes`. `created_by` stays empty until user logins are added.

For Streamlit Cloud, configure secrets in the deployment environment rather than uploading `.env` to GitHub.

---

## 8. Running locally

From the project directory:

```powershell
cd "C:\Users\Lenovo\OneDrive - Vytauto Didžiojo universitetas\Desktop\chisels_play"
```

Install/update packages:

```powershell
python -m pip install -r requirements.txt
```

Run Streamlit:

```powershell
python -m streamlit run app.py
```

Streamlit will print the local URL in the terminal.

Stop the application with:

```text
Ctrl + C
```

---

## 9. Recommended testing checklist

Before pushing a new version, test all of the following.

### Basic extraction

- upload a real PDF drawing;
- confirm furniture name/type;
- confirm overall dimensions when available;
- confirm component list;
- confirm obviously missing values are not invented.

### Hinges

- test a wardrobe with confirmed hinged doors;
- verify 2200 mm door -> 5 hinges per door;
- verify sliding doors do not receive hinges;
- verify explicit BOM hinges are not duplicated;
- verify missing door height produces uncertainty rather than an invented count.

### Catalog matching

- verify normal parts receive reasonable catalog matches;
- manually change at least one catalog selection;
- verify learned feedback still works as expected.

### NO MATCH workflow

Test all paths:

- select an existing catalog item;
- check/use a market price;
- create a custom item, change its price/name, then save it to the catalog and confirm only the final values were saved;
- analyse a new drawing and confirm no custom item or NO MATCH choice carries over from the previous one;
- ignore a component;
- intentionally leave a component unresolved.

### Needs Review

Verify warnings appear for relevant cases and disappear when the underlying issue is resolved.

Confirm warnings do **not** block quotation generation.

### Market-price check

- search a real component;
- review offers and units;
- verify catalog-vs-market comparison;
- verify a large difference can create a review warning;
- confirm market data is saved only when the estimator ticks Add to catalog and clicks Send ticked offers for catalog approval;
- confirm a sent item is not offered in the price list until an admin approves it on the Admin page;
- change the category after ticking and confirm only one item (with the final category) is saved;
- confirm the selected real offer is saved, not the market average;
- tick an offer, click Search again, and confirm the tick is cleared rather than moved to another offer.

### Calculation

- compare quantity × unit price manually;
- verify category coefficient;
- verify global project multiplier;
- verify calculated selling price;
- edit final client price and confirm the internal calculated price remains unchanged.

### Quotation

- enter client name;
- enter quote number;
- verify `DD/MM/YYYY` dates;
- add measurement/delivery/installation or another service;
- test fixed and percentage discount;
- verify VAT;
- verify preview totals;
- generate PDF;
- open PDF and visually inspect logo alignment, table, totals and footer;
- verify Lithuanian letters (for example `PASIŪLYMAS`, `Lekėčiai`) display correctly on the deployed app;
- verify a client name containing `&` prints completely;
- verify the PDF total equals the preview total;
- click Clear product and confirm the previous PDF can no longer be downloaded.

### Warning-only ready-to-quote behavior

Generate a quotation with a deliberate warning and confirm:

- the warning is clearly displayed;
- the PDF button still works;
- the warning itself is not printed into the client PDF.

---

## 10. Git workflow

Check current state first:

```powershell
git status
```

Stage intended files only:

```powershell
git add app.py catalog_store.py pdf_extractor.py quote_generator.py requirements.txt README.md data/catalog_additions.json
```

Add any other deliberately changed project files separately.

Never stage `.env`.

Commit:

```powershell
git commit -m "Simplify no-match workflow and add catalog saving"
```

Check remote state before pushing:

```powershell
git fetch origin
git status
```

If the branch is ahead of `origin/main` and not diverged:

```powershell
git push origin main
```

If Git reports a divergence, conflict, rebase in progress or cherry-pick in progress, resolve that state before pushing rather than doing an automatic pull/reset blindly.

---

## 11. Current limitations / intentionally deferred work

The following are intentionally not part of the current update:

- quantity override;
- quantity-calculation explanation UI;
- catalog price-age tracking;
- user logins and roles (the Admin page uses one shared password for now; `created_by` is not filled yet);
- client authentication/portal;
- project history/database;
- approval workflow;
- automatic quote sending.

Custom items are project-use items by default; items sent to the catalog are used in future estimates only after an admin approves them.

The ready-to-quote check is deliberately warning-only during testing.

---

## 12. Future architecture direction

If the application becomes client-facing, recommended separation is:

```text
Client portal
    ↓
Project upload
    ↓
AI extraction
    ↓
Catalog + learned matching
    ↓
Exception/review workflow
    ↓
Estimator approval
    ↓
Final client price
    ↓
Quotation PDF
    ↓
Client acceptance
```

Likely future modules:

- Projects/history;
- authenticated users and roles;
- Admin/Catalog panel;
- pending catalog-item approval;
- client portal;
- quote status/acceptance;
- audit trail;
- persistent database-backed catalog;
- usage/API-cost controls;
- multilingual client interface.

For a future public/client version, internal information such as raw catalog costs, coefficients, AI confidence, market-search internals and learned corrections should remain hidden from clients.

---

## 13. Data and security notes

- Never commit API keys or `.env`.
- Treat uploaded drawings and client quotations as confidential business/client information.
- Do not expose raw local filesystem paths in the client UI.
- Market-price results should be treated as external reference data and reviewed before commercial use.
- Human review remains important while the estimator is still being tested.
- Permanent catalog changes should eventually require controlled/admin access rather than being written automatically by ordinary users.

---

## 14. Current quotation supplier information

The PDF generator currently uses the following fixed supplier block:

```text
IĮ "Buki kaltai"
305189636
LT100012484610
Šilo g. 6, Lekėčiai, Šakių r.
062533752
```

The logo is loaded from:

```text
data/logo.jpg
```

If company details change, update the quotation-generator constants intentionally and test a rendered PDF afterward.

---

## 15. Development principle

The calculator should be treated as:

> **AI prepares the estimate; deterministic code calculates prices; the estimator resolves exceptions and controls the final commercial price.**

The goal is not to hide uncertainty. The goal is to make uncertainty obvious and fast to review while keeping the final pricing calculation deterministic and auditable.
