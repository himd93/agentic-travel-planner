import streamlit as st
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
import requests
from icalendar import Calendar, Event
from datetime import datetime, timedelta
import re
import tempfile
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


# -------------------- Search Tool --------------------
@tool
def search_google(query: str) -> str:
    """Search for travel information using SerpAPI."""
    serp_api_key = os.getenv("SERP_API_KEY", "")
    if not serp_api_key:
        return "Error: SERP_API_KEY not configured"
    
    try:
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "api_key": serp_api_key
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Extract organic results
        results = []
        if "organic_results" in data:
            for result in data["organic_results"][:5]:  # Top 5 results
                results.append(f"- {result.get('title', 'N/A')}: {result.get('snippet', 'N/A')}")
        
        return "\n".join(results) if results else "No results found"
    except Exception as e:
        return f"Search failed: {str(e)}"


# -------------------- ICS Helper --------------------
def generate_ics_content(plan_text: str, start_date: datetime = None) -> bytes:
    # Build a calendar in memory so Streamlit can offer it directly as a download.
    cal = Calendar()
    cal.add('prodid', '-Agentic FlyTripper-')
    cal.add('version', '2.0')


    if start_date is None:
        start_date = datetime.today()


    # The agents return labeled day sections; remove the wrapper before parsing them.
    plan_text_without_final_answer = plan_text.replace("Final Answer:", "").strip()
    # Capture each day's text until the next "Day N" heading or the end of the plan.
    day_pattern = re.compile(r'Day (\d+)[:\s]+(.*?)(?=Day \d+|$)', re.DOTALL)
    days = day_pattern.findall(plan_text_without_final_answer)


    if not days:
        # Keep the complete response usable even if the model does not follow the
        # expected day-by-day format.
        event = Event()
        event.add('summary', "Travel Itinerary")
        event.add('description', plan_text_without_final_answer)
        event.add('dtstart', start_date.date())
        event.add('dtend', start_date.date())
        event.add("dtstamp", datetime.now())
        cal.add_component(event)
    else:
        for day_num, day_content in days:
            day_num = int(day_num)
            # Day 1 maps to the selected start date; later days are offset from it.
            current_date = start_date + timedelta(days=day_num - 1)
            event = Event()
            event.add('summary', f"Day {day_num} Itinerary")
            event.add('description', day_content.strip())
            event.add('dtstart', current_date.date())
            event.add('dtend', current_date.date())
            event.add("dtstamp", datetime.now())
            cal.add_component(event)


    return cal.to_ical()


# -------------------- Agent Classes --------------------
class Agent:
    def __init__(self, name, model, tools=None, instructions=None):
        self.name = name
        self.model = model
        self.tools = tools or []
        self.instructions = instructions or []


    def run(self, message, **kwargs):
        # The model receives the agent role, tool descriptions, and task together.
        # Tool descriptions guide the model but do not execute tools automatically.
        tool_text = ""
        if self.tools:
            tool_text = "You have access to tools:\n"
            for tool in self.tools:
                tool_text += f"- {tool['name']}: {tool['description']}\n"
        full_prompt = "\n".join(self.instructions) + "\n" + tool_text + f"\nMessage: {message}"
        return self.model.invoke(full_prompt).content


# -------------------- Streamlit UI --------------------
st.title("✈️ Agentic FlyTripper")
st.caption("Plan your next adventure with multiple collaborative AI agents!")


# Streamlit reruns this script after each interaction, so keep the latest result
# in session state if the UI needs to retain it between reruns.
if 'itinerary' not in st.session_state:
   st.session_state.itinerary = None


# Read keys once into session state, while allowing the .env file to provide the
# initial values. The app cannot continue without both external services.
if "gemini_api_key" not in st.session_state:
   st.session_state.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
if "serp_api_key" not in st.session_state:
   st.session_state.serp_api_key = os.getenv("SERP_API_KEY", "")






gemini_key = st.session_state.gemini_api_key
serp_key = st.session_state.serp_api_key


if not gemini_key or not serp_key:
   st.warning("Please provide both Gemini and SerpAPI keys to continue.")
   st.stop()


# A single Gemini client is shared by all agents; each agent differs by prompt.
gemini_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=gemini_key)
search_tool_dict = {"name": "search_google", "func": search_google,
              "description": "Search for travel activities, hotels, restaurants, and attractions."}


# Initialize Agents
researcher_agent = Agent(
   name="Researcher Agent",
   model=gemini_llm,
   tools=[search_tool_dict],
   instructions=[
       "You are the **Researcher Agent** for a travel planning system.",
       "Your goal is to collect **reliable, up-to-date travel information** for the user’s trip.",
       "You have access to the following tool(s):",
       "- Search tool: find attractions, hotels, restaurants, transportation options, and local insights.",
       "",
       "🔹 Instructions:",
       "1. Use the search tool where necessary (do not invent information).",
       "2. Focus on high-quality, verified results (official websites, top review sources, reliable blogs).",
       "3. Summarize findings in a structured way:",
       "   - Attractions (with highlights)",
       "   - Hotels/Accommodations",
       "   - Restaurants/Food options",
       "   - Travel/Transport tips",
       "4. Keep descriptions concise but informative (2–4 sentences per item).",
       "5. Avoid adding your own opinions; base all output on retrieved sources.",
       "",
       "Output must be a clear **research summary**. Do NOT create an itinerary."
   ]
)




planner_agent = Agent(
   name="Planner Agent",
   model=gemini_llm,
   instructions=[
       "You are the **Planner Agent**. Your responsibility is to transform research results into a clear travel plan.",
       "",
       "🔹 Instructions:",
       "1. Create a day-by-day itinerary for the trip.",
       "2. Each day should include a balance of attractions, food options, and rest time.",
       "3. Ensure logical sequencing (minimize travel time, group nearby spots together).",
       "4. Respect the number of days requested by the user.",
       "5. Do not invent new information — only use the research provided.",
       "6. Ensure each day has a clear theme or highlight (morning, afternoon, evening activities).",
       "7. Write in an engaging but professional tone.",
       "",
       "🔹 Format Requirement:",
       "Wrap the final response in this format:",
       "Final Answer:",
       "Day 1: ...",
       "Day 2: ...",
       "Day 3: ...",
       "...",
       "",
       "Only provide the itinerary, nothing else."
   ]
)




optimizer_agent = Agent(
   name="Optimizer Agent",
   model=gemini_llm,
   instructions=[
       "You are the **Optimizer Agent**. Your task is to refine the draft itinerary for maximum usability.",
       "",
       "🔹 Instructions:",
       "1. Improve the itinerary for efficiency (minimize unnecessary travel).",
       "2. Adjust based on user preferences (e.g., prefers museums over nightlife).",
       "3. Ensure realistic timings (no overpacked days, allow travel/rest time).",
       "4. Add small enhancements (hidden gems, local experiences) where appropriate.",
       "5. Keep it feasible and enjoyable for a real traveler.",
       "",
       "🔹 Format Requirement:",
       "Output the refined itinerary in the same day-by-day format, preserving:",
       "Final Answer:",
       "Day 1: ...",
       "Day 2: ...",
       "..."
   ]
)




qa_agent = Agent(
   name="QA Agent",
   model=gemini_llm,
   instructions=[
       "You are the **Quality Assurance Agent** for travel itineraries.",
       "Your responsibility is to **validate and correct** the final plan before presenting it to the user.",
       "",
       "🔹 Instructions:",
       "1. Check factual accuracy: remove hallucinations, unrealistic claims, or non-existent places.",
       "2. Verify consistency: ensure attractions match the destination and sequence makes sense.",
       "3. Ensure clarity: rewrite vague items (e.g., 'visit museum' → 'Visit the Louvre Museum').",
       "4. Confirm that each day is feasible (not overloaded, includes travel time).",
       "5. Keep output user-friendly and safe (avoid unsafe areas or misleading advice).",
       "",
       "🔹 Format Requirement:",
       "Output the final verified itinerary, preserving the format:",
       "Final Answer:",
       "Day 1: ...",
       "Day 2: ...",
       "..."
   ]
)




# -------------------- Router / Orchestrator --------------------
def agentic_travel_planner(destination, num_days, preferences=""):
    # Each stage consumes the previous stage's text output and adds a new review
    # layer. Keeping the orchestration here makes the workflow easy to follow.
    # Step 1: Research current destination information.
   research_prompt = f"Research {destination} for a {num_days}-day trip. Include attractions, hotels, restaurants."
   research_results = researcher_agent.run(research_prompt)


    # Step 2: Turn research into a day-by-day draft itinerary.
   planner_prompt = f"Create a {num_days}-day itinerary from this research:\n{research_results}"
   itinerary = planner_agent.run(planner_prompt)


    # Step 3: Adjust the draft for the traveler's stated preferences.
   optimizer_prompt = f"Optimize this itinerary for user preferences: {preferences}\n{itinerary}"
   optimized_itinerary = optimizer_agent.run(optimizer_prompt)


    # Step 4: Perform a final factual, consistency, and feasibility review.
   qa_prompt = f"Verify and correct this itinerary:\n{optimized_itinerary}"
   verified_itinerary = qa_agent.run(qa_prompt)


    # Step 5: Convert the verified text into calendar events for download.
   ics_file = generate_ics_content(verified_itinerary)


   return verified_itinerary, ics_file


# -------------------- User Input --------------------
destination = st.text_input("Destination:")
num_days = st.number_input("Number of days:", min_value=1, max_value=30, value=7)
preferences = st.text_area("Preferences (optional):", placeholder="E.g., prefer museums over nightlife")


if st.button("Generate Itinerary"):
   if not destination:
    # Avoid spending API calls when the required destination is missing.
       st.warning("Please enter a destination.")
   else:
       with st.spinner("Planning your trip with agentic AI..."):
           itinerary, ics_file = agentic_travel_planner(destination, num_days, preferences)
           st.session_state.itinerary = itinerary
           # The model is asked to include "Final Answer:"; hide that label in
           # the UI when it is present, but still display unexpected output.
           if "Final Answer:" in itinerary:
               st.markdown(itinerary.split("Final Answer:")[1].strip())
           else:
               st.markdown(itinerary)
           # The bytes returned by generate_ics_content are sent directly to the
           # browser without creating a temporary file on disk.
           st.download_button("📅 Download Itinerary (.ics)", ics_file, "itinerary.ics", "text/calendar")


