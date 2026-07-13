"""
app.py — bare shell, Week 1 exit criteria.

Just a chat loop talking to Gemini. No Gmail/Drive/Sheets tools yet,
no OAuth flow wired in here yet (that comes back in once we add tools
that touch your Google account). This step exists purely to prove:
Streamlit <-> Gemini works, before we add any complexity on top.
"""

import os
import streamlit as st
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage

load_dotenv()

if not os.getenv("GOOGLE_API_KEY"):
    st.error("GOOGLE_API_KEY not found. Check your .env file.")
    st.stop()

st.title("ReconAI — bare shell test")
st.caption("No tools wired in yet. Just confirming Gemini responds in Streamlit.")

# Cache the LLM instance across reruns instead of recreating it every keystroke
if "llm" not in st.session_state:
    st.session_state.llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0.3,
    )

if "messages" not in st.session_state:
    st.session_state.messages = []

# Render existing chat history
for msg in st.session_state.messages:
    role = "user" if isinstance(msg, HumanMessage) else "assistant"
    st.chat_message(role).write(msg.content)

# Handle new input
if prompt := st.chat_input("Ask Gemini anything, just to confirm wiring works"):
    st.session_state.messages.append(HumanMessage(content=prompt))
    st.chat_message("user").write(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            response = st.session_state.llm.invoke(st.session_state.messages)
        st.write(response.content)

    st.session_state.messages.append(AIMessage(content=response.content))
