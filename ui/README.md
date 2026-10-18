# Optional API client

This directory contains an optional Streamlit client for the existing FastAPI API. The primary product interface is served from `static/`; this client does not replace it or introduce another backend.

Install `ui/requirements.txt` in an appropriate development environment, start the API separately and run:

```bash
streamlit run ui/app.py
```

Configure the backend through the settings supported in `ui/app.py`. Network failures must remain visible; the client must not invent answers or citations. Use `tests/test_streamlit_flow.py` for its isolated interaction checks. Model quality requires separate evaluation.
