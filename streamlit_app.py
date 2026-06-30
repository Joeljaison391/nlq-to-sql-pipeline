from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import load_settings
from src.pipeline import AnalyticsPipeline

st.set_page_config(page_title="Gaming & Mental Health Analytics", page_icon="🎮", layout="wide")
st.title("🎮 Gaming & Mental Health - Ask in Plain English")
st.caption("Type a question about the survey data and get a plain-English answer.")


@st.cache_resource(show_spinner="Starting up...")
def get_pipeline():
    load_settings()
    return AnalyticsPipeline()


EXAMPLE_QUESTIONS = [
    "How does gaming addiction level vary between genders?",
    "Which age groups report the highest addiction levels?",
    "What is the average anxiety score for each gender?",
    "How many respondents have high addiction level (>= 5)?",
]

with st.sidebar:
    st.header("About this dataset")
    st.write("Single table with one row per survey respondent: demographics, gaming habits, and mental-health scores.")
    st.subheader("Try an example")
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, use_container_width=True):
            st.session_state["question_input"] = q

question = st.text_input("Your question", key="question_input", placeholder="e.g. Which age group has the lowest average anxiety score?")
ask_clicked = st.button("Ask", type="primary")

if ask_clicked and question.strip():
    try:
        pipeline = get_pipeline()
    except RuntimeError as exc:
        st.error(f"Setup error: {exc}")
        st.stop()

    with st.spinner("Thinking..."):
        result = pipeline.run(question.strip())

    status_styles = {
        "success": st.success,
        "unanswerable": st.warning,
        "invalid_sql": st.warning,
        "error": st.error,
    }
    status_styles.get(result.status, st.info)(f"Status: **{result.status}**")

    st.subheader("Answer")
    st.write(result.answer)

    with st.expander("Generated SQL"):
        if result.sql:
            st.code(result.sql, language="sql")
        else:
            st.write("No SQL was generated.")
        if result.sql_validation.error:
            st.caption(f"Validation note: {result.sql_validation.error}")

    with st.expander(f"Result rows ({result.sql_execution.row_count})"):
        if result.rows:
            st.dataframe(pd.DataFrame(result.rows), use_container_width=True)
        else:
            st.write("No rows returned.")

    with st.expander("Performance & token usage"):
        col1, col2, col3 = st.columns(3)
        col1.metric("Total time", f"{result.timings['total_ms']:.0f} ms")
        col2.metric("LLM calls", result.total_llm_stats.get("llm_calls", 0))
        col3.metric("Total tokens", result.total_llm_stats.get("total_tokens", 0))
        st.json(result.timings)
        st.json(result.total_llm_stats)

elif ask_clicked:
    st.warning("Please type a question first.")
