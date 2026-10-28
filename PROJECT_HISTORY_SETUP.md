# Project History setup

1. In Supabase, open **SQL Editor**.
2. Run `project_history_schema.sql` once. This creates the `projects` table and service-role permissions.
3. Copy these files into the repository:
   - `app.py`
   - `project_store.py`
   - `ui.py`
   - `pages/Projects.py`
   - `supabase_schema.sql`
   - `project_history_schema.sql`
   - `README.md`
4. Keep the existing `pages/Admin.py` unchanged.
5. Run locally:

```powershell
python -m streamlit run app.py
```

## Test flow

1. Name a project.
2. Analyze a drawing and create/use an estimate.
3. Enter client quotation fields.
4. Set project status.
5. Click **Save project** near the bottom of Calculator.
6. Open **Projects** from the sidebar.
7. Confirm the project, client, prices and status are shown.
8. Click **Open project**.
9. Change the final client price or status.
10. Click **Save project** again.
11. Return to Projects and verify there is still only one record and that it contains the updated values.
12. Test **Archive**.

Project history does not currently save the uploaded drawing file or the generated PDF itself.
