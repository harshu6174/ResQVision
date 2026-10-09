"""Start the Damage Analyzer.

    python app.py          (same as: python server.py)

The old Streamlit interface was replaced by the new web interface in web/.
If you launched this file with `streamlit run app.py`, you will only see a reminder page, not the app.
"""
try:
    from streamlit.runtime import exists as _in_streamlit
    IN_STREAMLIT = _in_streamlit()
except Exception:
    IN_STREAMLIT = False

if IN_STREAMLIT:
    import streamlit as st
    st.set_page_config(page_title="Damage Analyzer")
    st.title("This is not the new interface")
    st.write("The new Damage Analyzer is a separate web app. Stop Streamlit (Ctrl+C), then run:")
    st.code("python server.py")
    st.write("and open http://127.0.0.1:8000")
elif __name__ == "__main__":
    import server
    server.main()
