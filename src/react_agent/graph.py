"""Define a custom Reasoning and Action agent.

Works with a chat model with tool calling support.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Literal, cast

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.prompts import SRE_REVIEWER_PROMPT
from react_agent.regex_rules import check_regex_patterns
from react_agent.state import InputState, State
from react_agent.tools import TOOLS
from react_agent.utils import load_chat_model

# Define the function that calls the model


async def call_model(
    state: State, runtime: Runtime[Context]
) -> Dict[str, List[AIMessage]]:
    """Call the LLM powering our "agent".

    This function prepares the prompt, initializes the model, and processes the response.

    Args:
        state (State): The current state of the conversation.
        config (RunnableConfig): Configuration for the model run.

    Returns:
        dict: A dictionary containing the model's response message.
    """
    # Initialize the model with tool binding. Change the model or add more tools here.
    model = load_chat_model(runtime.context.model).bind_tools(TOOLS)

    # Format the system prompt. Customize this to change the agent's behavior.
    system_message = runtime.context.system_prompt.format(
        system_time=datetime.now(tz=UTC).isoformat()
    )

    # Get the model's response
    response = cast(  # type: ignore[redundant-cast]
        AIMessage,
        await model.ainvoke(
            [{"role": "system", "content": system_message}, *state.messages]
        ),
    )

    # Heuristic for smaller models that output raw JSON instead of native tool calls
    if not response.tool_calls and isinstance(response.content, str):
        try:
            import json
            import uuid

            content_str = response.content.strip()
            # Remove Markdown JSON wrapper if it exists
            if content_str.startswith("```json") and content_str.endswith("```"):
                content_str = content_str[7:-3].strip()
            elif content_str.startswith("```") and content_str.endswith("```"):
                content_str = content_str[3:-3].strip()

            data = json.loads(content_str)
            if isinstance(data, dict) and "name" in data and "arguments" in data:
                args = data["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                response.tool_calls = [
                    {
                        "name": data["name"],
                        "args": args,
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                    }
                ]
        except Exception:
            pass

    # Handle the case when it's the last step and the model still wants to use a tool
    if state.is_last_step and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content="Sorry, I could not find an answer to your question in the specified number of steps.",
                )
            ]
        }

    # Return the model's response as a list to be added to existing messages
    return {"messages": [response]}


# Add Regex precheck node
async def regex_precheck(
    state: State, runtime: Runtime[Context]
) -> Dict[str, List[AnyMessage]]:
    """Checks if the log matches any known fast-path regex rules."""
    report = check_regex_patterns(state.raw_log)
    if report:
        # If high confidence, bypass LLM and end graph
        if report.get("confidence") == "high":
            return {"messages": [AIMessage(content=json.dumps(report, indent=2))]}

        # If medium/low confidence, inject a guide message for the LLM
        hint = (
            f"I obtained this classification through string matching: {json.dumps(report, indent=2)}.\n"
            "Look at the log content and refer to this information to derive the final result. "
            "If the error classification is wrong, suggest a new error classification "
            "and add the grounds for that judgment as an explanation."
        )
        return {"messages": [HumanMessage(content=hint)]}

    # If no match, return no new messages; the graph will route to preprocess_log
    return {"messages": []}


async def preprocess_log(state: State, runtime: Runtime[Context]) -> Dict[str, Any]:
    """Distills the raw log into a more concise format for the LLM and search tools."""
    raw_log = state.raw_log

    # 1. Traceback extraction (most relevant part)
    if "Traceback (most recent call last):" in raw_log:
        distilled_log = raw_log.split("Traceback (most recent call last):")[-1]
    else:
        # If no Traceback, take last 2000 chars as a heuristic
        distilled_log = raw_log[-2000:]

    # 2. Remove Jupyter noise (e.g., "Input In [1], in <cell line: 5>...")
    distilled_log = re.sub(r"Input In \[\d+\].+", "", distilled_log)

    # 3. Clean empty lines and limit to last 50 lines
    lines = [line.strip() for line in distilled_log.split("\n") if line.strip()]
    final_log = "\n".join(lines[-50:])

    # Update messages to replace the long log with the distilled version
    # This helps the LLM generate better search queries.
    new_messages = []
    if state.messages and isinstance(state.messages[0], HumanMessage):
        original_content = state.messages[0].content
        if raw_log in original_content:
            new_content = original_content.replace(raw_log, final_log)
            # We create a new HumanMessage with the same ID to replace it via add_messages
            new_messages.append(
                HumanMessage(content=new_content, id=state.messages[0].id)
            )

    return {"raw_log": final_log, "messages": new_messages}


async def review_analysis(state: State, runtime: Runtime[Context]) -> Dict[str, Any]:
    """Reviews the agent's analysis and requests refinement if necessary."""
    # Prevent infinite loops: only review once.
    if state.is_refined:
        return {}

    # 1. Get the last message (the analysis report)
    last_message = state.messages[-1]
    analysis_content = last_message.content

    # 2. Call the model with the reviewer prompt
    model = load_chat_model(runtime.context.model)
    prompt = SRE_REVIEWER_PROMPT.format(analysis=analysis_content)

    response = await model.ainvoke([{"role": "user", "content": prompt}])

    try:
        # Heuristic for JSON extraction from model output
        content = response.content
        if "```json" in content:
            content = content.split("```json")[-1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[-1].split("```")[0]

        review_data = json.loads(content.strip())

        if not review_data.get("is_approved", True):
            feedback = review_data.get("feedback", "Please refine the analysis.")
            # Inject a refinement request into the conversation
            feedback_msg = HumanMessage(
                content=f"Review Feedback: {feedback}\nPlease refine the analysis and include a 'metadata' field in your JSON output with 'is_refined': true and 'review_feedback': '{feedback}'."
            )
            return {
                "messages": [feedback_msg],
                "is_refined": True,
                "review_feedback": feedback,
            }
        else:
            # If approved, inject the review result into the final analysis JSON
            try:
                final_analysis = json.loads(analysis_content)
                final_analysis["review_result"] = review_data.get("feedback", "Approved by Lead SRE.")
                # Update the last message to include the review result
                return {"messages": [AIMessage(content=json.dumps(final_analysis, indent=2), id=last_message.id)]}
            except Exception:
                pass
    except Exception:
        pass

    return {}


def route_after_review(state: State) -> Literal["__end__", "call_model"]:
    """Determines if the graph should end or go back for refinement."""
    # If the last message is from a Human (the reviewer feedback), we go back to call_model
    if state.messages and isinstance(state.messages[-1], HumanMessage):
        if "Review Feedback" in state.messages[-1].content:
            return "call_model"
    return "__end__"


def route_after_regex(state: State) -> Literal["preprocess_log", "__end__"]:
    """Routes based on whether regex found a match."""
    # If the last message is an AIMessage containing our JSON report, we are done
    if state.messages and isinstance(state.messages[-1], AIMessage):
        try:
            # Simple heuristic: if it parsed as JSON and has error_id, it's our regex output
            data = json.loads(state.messages[-1].content)
            if "error_id" in data:
                return "__end__"
        except Exception:
            pass

    return "preprocess_log"


# Define a new graph
builder = StateGraph(State, input_schema=InputState, context_schema=Context)

# Define nodes
builder.add_node("regex_precheck", regex_precheck)
builder.add_node("preprocess_log", preprocess_log)
builder.add_node("call_model", call_model)
builder.add_node("review_analysis", review_analysis)
builder.add_node("tools", ToolNode(TOOLS))

# 1. Entrypoint is now strictly the regex precheck
builder.add_edge("__start__", "regex_precheck")

# 2. Condition after regex precheck -> Either end the graph or preprocess log
builder.add_conditional_edges("regex_precheck", route_after_regex)

# 3. From preprocessing, go to model
builder.add_edge("preprocess_log", "call_model")


# 3. From call_model, we route to either TOOLS or REVIEW
def route_model_output(state: State) -> Literal["review_analysis", "tools"]:
    """Determine the next node based on the model's output."""
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"Expected AIMessage in output edges, but got {type(last_message).__name__}"
        )
    if not last_message.tool_calls:
        return "review_analysis"
    return "tools"


builder.add_conditional_edges("call_model", route_model_output)
builder.add_conditional_edges("review_analysis", route_after_review)
builder.add_edge("tools", "call_model")

# Compile the builder into an executable graph
graph = builder.compile(name="ReAct Agent")
