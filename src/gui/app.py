# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI Dashboard - Streamlit-based administration and monitoring UI.

Panes:
- Overview: system health, domain distribution, quick stats
- Artifacts: browse, filter, recategorize ingested files
- Query: test multi-domain queries with result visualization
- Audit: activity timeline, ingestion stats, cost tracking
- Maintenance: health checks, stale detection, orphan cleanup
"""

import json
from datetime import datetime

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MCP_URL = "http://ai-companion-mcp:8888"

st.set_page_config(
    page_title="Cerid AI Dashboard",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(path: str, params: dict = None) -> dict:
    """GET request to MCP server."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{MCP_URL}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return {}


def api_post(path: str, data: dict = None) -> dict:
    """POST request to MCP server."""
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{MCP_URL}{path}", json=data or {})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return {}


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.title("Cerid AI")
st.sidebar.caption("Personal Knowledge Companion")

page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Artifacts", "Query", "Audit", "Maintenance"],
    index=0,
)


# ===================================================================
# PAGE: Overview
# ===================================================================

if page == "Overview":
    st.title("System Overview")

    # Health check
    health = api_get("/health")
    if health:
        col1, col2, col3 = st.columns(3)
        services = health.get("services", {})
        overall = health.get("status", "unknown")

        col1.metric("ChromaDB", services.get("chromadb", "?"))
        col2.metric("Neo4j", services.get("neo4j", "?"))
        col3.metric("Redis", services.get("redis", "?"))

        if overall == "healthy":
            st.success("All services healthy")
        else:
            st.warning(f"System status: {overall}")

    st.divider()

    # Domain distribution
    st.subheader("Knowledge Base Distribution")
    rectify_data = api_post("/agent/rectify", {"checks": ["distribution"]})
    findings = rectify_data.get("findings", {})
    dist = findings.get("distribution", {})

    if dist:
        distribution = dist.get("distribution", {})
        if distribution:
            df = pd.DataFrame([
                {
                    "Domain": domain,
                    "Artifacts": info.get("artifacts", 0),
                    "Chunks": info.get("chunks", 0),
                }
                for domain, info in distribution.items()
            ])

            col1, col2 = st.columns(2)
            with col1:
                fig = px.bar(
                    df, x="Domain", y="Artifacts",
                    title="Artifacts by Domain",
                    color="Domain",
                )
                fig.update_layout(showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

            with col2:
                fig = px.pie(
                    df, names="Domain", values="Chunks",
                    title="Chunks by Domain",
                )
                st.plotly_chart(fig, use_container_width=True)

            # Summary metrics
            total_a = dist.get("total_artifacts", 0)
            total_c = dist.get("total_chunks", 0)
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Artifacts", total_a)
            col2.metric("Total Chunks", total_c)
            col3.metric("Domains", dist.get("domain_count", 0))

    st.divider()

    # Collections
    st.subheader("ChromaDB Collections")
    collections = api_get("/collections")
    if collections:
        st.write(f"**{collections.get('total', 0)}** collections")
        cols = collections.get("collections", [])
        if cols:
            st.code(", ".join(cols))


# ===================================================================
# PAGE: Artifacts
# ===================================================================

elif page == "Artifacts":
    st.title("Artifact Browser")

    col1, col2 = st.columns([1, 3])
    with col1:
        domain_filter = st.selectbox(
            "Domain",
            ["All", "coding", "finance", "projects", "personal", "general"],
        )
        limit = st.slider("Limit", 10, 200, 50)

    # Fetch artifacts
    params = {"limit": limit}
    if domain_filter != "All":
        params["domain"] = domain_filter
    artifacts = api_get("/artifacts", params)

    with col2:
        if isinstance(artifacts, list) and artifacts:
            df = pd.DataFrame(artifacts)
            display_cols = ["filename", "domain", "chunk_count", "ingested_at"]
            available = [c for c in display_cols if c in df.columns]
            st.dataframe(df[available], use_container_width=True, height=400)

            # Recategorize UI
            st.divider()
            st.subheader("Recategorize Artifact")
            artifact_ids = df["id"].tolist() if "id" in df.columns else []
            if artifact_ids:
                selected_id = st.selectbox("Artifact ID", artifact_ids)
                new_domain = st.selectbox(
                    "New Domain",
                    ["coding", "finance", "projects", "personal", "general"],
                    key="recat_domain",
                )
                if st.button("Move Artifact"):
                    result = api_post("/recategorize", {
                        "artifact_id": selected_id,
                        "new_domain": new_domain,
                    })
                    if result.get("status") == "success":
                        st.success(
                            f"Moved to {result.get('new_domain')} "
                            f"({result.get('chunks_moved')} chunks)"
                        )
                    else:
                        st.error(f"Failed: {result}")
        elif isinstance(artifacts, list):
            st.info("No artifacts found for this filter.")
        else:
            st.warning("Could not fetch artifacts.")


# ===================================================================
# PAGE: Query
# ===================================================================

elif page == "Query":
    st.title("Query Playground")

    query_text = st.text_area("Search Query", placeholder="e.g. tax deductions for home office")

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_domains = st.multiselect(
            "Domains",
            ["coding", "finance", "projects", "personal", "general"],
            default=[],
            help="Leave empty to search all domains",
        )
    with col2:
        top_k = st.slider("Results per domain", 1, 20, 5)
    with col3:
        use_reranking = st.checkbox("LLM Reranking", value=True)

    if st.button("Search", type="primary") and query_text:
        with st.spinner("Searching..."):
            payload = {
                "query": query_text,
                "top_k": top_k,
                "use_reranking": use_reranking,
            }
            if selected_domains:
                payload["domains"] = selected_domains

            result = api_post("/agent/query", payload)

        if result:
            # Metrics
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Results", result.get("total_results", 0))
            col2.metric("Confidence", f"{result.get('confidence', 0):.2%}")
            col3.metric("Context Size", f"{result.get('token_budget_used', 0):,} chars")
            col4.metric("Domains", len(result.get("domains_searched", [])))

            st.divider()

            # Sources
            sources = result.get("sources", [])
            if sources:
                st.subheader("Sources")
                for i, src in enumerate(sources):
                    with st.expander(
                        f"#{i+1} — {src.get('filename', '?')} "
                        f"({src.get('domain', '?')}) "
                        f"— relevance: {src.get('relevance', 0):.3f}"
                    ):
                        st.text(src.get("content", ""))
                        st.caption(
                            f"Artifact: {src.get('artifact_id', '?')} | "
                            f"Chunk: {src.get('chunk_index', '?')}"
                        )

            # Context preview
            context = result.get("context", "")
            if context:
                st.divider()
                st.subheader("Assembled Context")
                st.text_area("Context", context, height=200, disabled=True)


# ===================================================================
# PAGE: Audit
# ===================================================================

elif page == "Audit":
    st.title("Audit & Analytics")

    hours = st.slider("Time window (hours)", 1, 720, 24)

    if st.button("Generate Report", type="primary"):
        with st.spinner("Generating audit report..."):
            report = api_post("/agent/audit", {"hours": hours})

        if report:
            # Activity
            activity = report.get("activity", {})
            if activity:
                st.subheader("Activity Summary")
                col1, col2 = st.columns(2)
                col1.metric("Total Events", activity.get("total_events", 0))
                col2.metric("Scanned Entries", activity.get("scanned_entries", 0))

                # Event breakdown
                events = activity.get("event_breakdown", {})
                if events:
                    df_events = pd.DataFrame(
                        list(events.items()),
                        columns=["Event Type", "Count"],
                    )
                    fig = px.bar(
                        df_events, x="Event Type", y="Count",
                        title="Events by Type",
                        color="Event Type",
                    )
                    fig.update_layout(showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)

                # Timeline
                timeline = activity.get("hourly_timeline", {})
                if timeline:
                    df_time = pd.DataFrame(
                        list(timeline.items()),
                        columns=["Hour", "Events"],
                    )
                    fig = px.line(
                        df_time, x="Hour", y="Events",
                        title="Activity Timeline",
                    )
                    st.plotly_chart(fig, use_container_width=True)

            st.divider()

            # Ingestion stats
            ingestion = report.get("ingestion", {})
            if ingestion:
                st.subheader("Ingestion Statistics")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Ingests", ingestion.get("total_ingests", 0))
                col2.metric("Duplicates", ingestion.get("total_duplicates", 0))
                col3.metric("Duplicate Rate", f"{ingestion.get('duplicate_rate', 0):.1%}")
                col4.metric("Avg Chunks/File", ingestion.get("avg_chunks_per_file", 0))

                # File type distribution
                ext_dist = ingestion.get("file_type_distribution", {})
                if ext_dist:
                    df_ext = pd.DataFrame(
                        list(ext_dist.items()),
                        columns=["File Type", "Count"],
                    )
                    fig = px.pie(
                        df_ext, names="File Type", values="Count",
                        title="Ingested File Types",
                    )
                    st.plotly_chart(fig, use_container_width=True)

            st.divider()

            # Costs
            costs = report.get("costs", {})
            if costs:
                st.subheader("Cost Estimates")
                est_cost = costs.get("estimated_cost_usd", {})
                est_tokens = costs.get("estimated_tokens", {})
                ops = costs.get("operations", {})

                col1, col2, col3 = st.columns(3)
                col1.metric(
                    "Total Cost",
                    f"${est_cost.get('total', 0):.4f}",
                )
                col2.metric(
                    "Total Tokens",
                    f"{est_tokens.get('total', 0):,}",
                )
                col3.metric(
                    "AI Operations",
                    sum(ops.values()),
                )

                # Breakdown table
                breakdown = pd.DataFrame([
                    {
                        "Tier": tier.replace("categorize_", "").title(),
                        "Operations": ops.get(tier, 0),
                        "Tokens": est_tokens.get(tier.split("_")[-1] if "_" in tier else tier, 0),
                        "Cost ($)": est_cost.get(tier.split("_")[-1] if "_" in tier else tier, 0),
                    }
                    for tier in ["categorize_smart", "categorize_pro", "rerank"]
                ])
                st.dataframe(breakdown, use_container_width=True, hide_index=True)

            st.divider()

            # Query patterns
            queries = report.get("queries", {})
            if queries:
                st.subheader("Query Patterns")
                col1, col2 = st.columns(2)
                col1.metric("Total Queries", queries.get("total_queries", 0))
                col2.metric("Avg Results", queries.get("avg_results_per_query", 0))

                domain_freq = queries.get("domain_frequency", {})
                if domain_freq:
                    df_freq = pd.DataFrame(
                        list(domain_freq.items()),
                        columns=["Domain", "Queries"],
                    )
                    fig = px.bar(
                        df_freq, x="Domain", y="Queries",
                        title="Queries by Domain",
                        color="Domain",
                    )
                    fig.update_layout(showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)


# ===================================================================
# PAGE: Maintenance
# ===================================================================

elif page == "Maintenance":
    st.title("Maintenance & Health")

    col1, col2 = st.columns([2, 1])

    with col2:
        stale_days = st.number_input("Stale threshold (days)", 30, 365, 90)
        auto_purge = st.checkbox("Auto-purge (destructive)", value=False)
        if auto_purge:
            st.warning("Auto-purge will permanently remove stale artifacts and orphaned chunks.")

        actions = st.multiselect(
            "Actions",
            ["health", "stale", "collections", "orphans"],
            default=["health", "collections"],
        )

    with col1:
        if st.button("Run Maintenance", type="primary"):
            with st.spinner("Running maintenance..."):
                result = api_post("/agent/maintain", {
                    "actions": actions,
                    "stale_days": stale_days,
                    "auto_purge": auto_purge,
                })

            if result:
                # System health
                health = result.get("health", {})
                if health:
                    st.subheader("System Health")
                    overall = health.get("overall", "unknown")
                    if overall == "healthy":
                        st.success("All services healthy")
                    else:
                        st.warning(f"Status: {overall}")

                    services = health.get("services", {})
                    cols = st.columns(len(services))
                    for i, (svc, status) in enumerate(services.items()):
                        with cols[i]:
                            color = "green" if status == "connected" else "red"
                            st.markdown(
                                f"**{svc.title()}**  \n"
                                f":{color}[{status}]"
                            )

                    data = health.get("data", {})
                    if data:
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Artifacts", data.get("artifacts", "?"))
                        col2.metric("Total Chunks", data.get("total_chunks", "?"))
                        col3.metric("Audit Log", data.get("audit_log_entries", "?"))

                st.divider()

                # Stale artifacts
                stale = result.get("stale", {})
                if stale:
                    st.subheader(f"Stale Artifacts (>{stale_days} days)")
                    count = stale.get("count", 0)
                    if count == 0:
                        st.success("No stale artifacts found.")
                    else:
                        st.warning(f"{count} stale artifact(s) found")
                        artifacts = stale.get("artifacts", [])
                        if artifacts:
                            df = pd.DataFrame(artifacts)
                            st.dataframe(df, use_container_width=True, hide_index=True)

                    purge = result.get("purge_result", {})
                    if purge:
                        st.info(
                            f"Purged {purge.get('purged_count', 0)} artifact(s), "
                            f"{purge.get('error_count', 0)} error(s)"
                        )

                # Collections
                cols_data = result.get("collections", {})
                if cols_data:
                    st.subheader("Collection Analysis")
                    collection_info = cols_data.get("collections", {})
                    if collection_info:
                        df = pd.DataFrame([
                            {"Collection": name, "Chunks": info.get("chunks", 0)}
                            for name, info in collection_info.items()
                        ])
                        fig = px.bar(
                            df, x="Collection", y="Chunks",
                            title="Chunks per Collection",
                            color="Collection",
                        )
                        fig.update_layout(showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)

                    recs = cols_data.get("recommendations", [])
                    if recs:
                        st.subheader("Recommendations")
                        for rec in recs:
                            st.info(rec)

                # Orphans
                orphans = result.get("orphans", {})
                if orphans:
                    st.subheader("Orphaned Chunks")
                    count = orphans.get("count", 0)
                    if count == 0:
                        st.success("No orphaned chunks found.")
                    else:
                        st.warning(f"{count} orphaned chunk(s) found")
                        by_domain = orphans.get("by_domain", {})
                        if by_domain:
                            st.json(by_domain)

                    cleanup = result.get("orphan_cleanup", {})
                    if cleanup:
                        st.info(f"Cleaned: {cleanup}")