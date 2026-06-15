# Streamlit UI for Aizen

"""A minimal Streamlit interface to interact with the Aizen agent.

Run with:

    streamlit run aizen/streamlit_app.py

This UI provides a text box to send commands to the Aizen agent and displays the
responses. It is intentionally lightweight and uses the public API exposed in
`aizen.agent`.
"""

import streamlit as st

from aizen.agent import AgentRunner
from aizen.config import load_config

st.set_page_config(page_title="Aizen Assistant", layout="centered")

st.title("🧠 Aizen Assistant")
st.write(
    "This simple UI lets you interact with the Aizen agent. "
    "Enter a prompt below and see the agent's response."
)

# Load configuration (if any) – falls back to defaults.
config = load_config()
agent = AgentRunner(config=config)

if "history" not in st.session_state:
    st.session_state.history = []

prompt = st.text_area("Your prompt", height=150)
col1, col2 = st.columns([1, 1])
with col1:
    if st.button("Send") and prompt.strip():
        with st.spinner("Thinking..."):
            try:
                # We need to construct the messages array for AgentRunner
                messages = [{"role": "user", "content": prompt}]
                # Run the turn async
                import asyncio

                asyncio.run(agent.run_turn(messages))
                # The runner mutates messages in place, get the last assistant response
                response = (
                    messages[-1]["content"]
                    if messages[-1]["role"] == "assistant"
                    else "Error: No response generated"
                )
            except Exception as e:
                response = f"Error: {e}"
        st.session_state.history.append((prompt, response))
        st.rerun()

with col2:
    if st.button("Clear History"):
        st.session_state.history = []
        st.rerun()

if st.session_state.history:
    st.subheader("Conversation")
    for i, (q, a) in enumerate(reversed(st.session_state.history)):
        st.markdown(f"**You:** {q}")
        st.markdown(f"**Aizen:** {a}")
        if i != len(st.session_state.history) - 1:
            st.markdown("---")
