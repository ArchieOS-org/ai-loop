"""Linear API integration using GraphQL."""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ai_loop.config import get_settings
from ai_loop.core.models import LinearIssue

LINEAR_API_ENDPOINT = "https://api.linear.app/graphql"


class LinearClient:
    """Client for Linear GraphQL API."""

    def __init__(self, api_key: str | None = None):
        settings = get_settings()
        self.api_key = api_key or settings.linear_api_key
        self.timeout = settings.http_timeout_secs

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                LINEAR_API_ENDPOINT,
                headers=self._headers(),
                json={"query": query, "variables": variables or {}},
            )
            response.raise_for_status()
            data = response.json()
            if "errors" in data:
                raise ValueError(f"GraphQL errors: {data['errors']}")
            return data["data"]

    async def get_issue(self, identifier: str) -> LinearIssue:
        """Fetch a single issue by identifier (e.g., 'LIN-123')."""
        query = """
        query GetIssue($identifier: String!) {
            issue(id: $identifier) {
                id
                identifier
                title
                description
                state { name }
                priority
                team { id name }
                project { id name }
                labels { nodes { name } }
                url
            }
        }
        """
        # Try by identifier first
        try:
            data = await self._query(query, {"identifier": identifier})
            if data.get("issue"):
                return self._parse_issue(data["issue"])
        except Exception:
            pass

        # Fallback: search by identifier filter
        search_query = """
        query SearchIssue($filter: IssueFilter!) {
            issues(filter: $filter, first: 1) {
                nodes {
                    id
                    identifier
                    title
                    description
                    state { name }
                    priority
                    team { id name }
                    project { id name }
                    labels { nodes { name } }
                    url
                }
            }
        }
        """
        data = await self._query(
            search_query,
            {"filter": {"identifier": {"eq": identifier}}},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        if not nodes:
            raise ValueError(f"Issue not found: {identifier}")
        return self._parse_issue(nodes[0])

    async def list_issues(
        self,
        *,
        team: str | None = None,
        project: str | None = None,
        state: str = "Todo",
        label: str | None = None,
        limit: int = 20,
    ) -> list[LinearIssue]:
        """List issues matching filters."""
        filters: dict = {}
        if team:
            filters["team"] = {"name": {"eq": team}}
        if project:
            filters["project"] = {"name": {"eq": project}}
        if state:
            filters["state"] = {"name": {"eq": state}}
        if label:
            filters["labels"] = {"name": {"eq": label}}

        query = """
        query ListIssues($filter: IssueFilter!, $first: Int!) {
            issues(
                filter: $filter,
                first: $first,
                orderBy: [
                    { priority: { order: Descending, nullsLast: true } },
                    { updatedAt: Descending }
                ]
            ) {
                nodes {
                    id
                    identifier
                    title
                    description
                    state { name }
                    priority
                    team { id name }
                    project { id name }
                    labels { nodes { name } }
                    url
                }
            }
        }
        """
        data = await self._query(query, {"filter": filters, "first": limit})
        nodes = data.get("issues", {}).get("nodes", [])
        return [self._parse_issue(node) for node in nodes]

    async def add_comment(self, issue_id: str, body: str) -> bool:
        """Add a comment to an issue."""
        mutation = """
        mutation AddComment($issueId: String!, $body: String!) {
            commentCreate(input: { issueId: $issueId, body: $body }) {
                success
            }
        }
        """
        data = await self._query(mutation, {"issueId": issue_id, "body": body})
        return data.get("commentCreate", {}).get("success", False)

    def _parse_issue(self, node: dict) -> LinearIssue:
        """Parse API response into LinearIssue."""
        return LinearIssue(
            id=node["id"],
            identifier=node["identifier"],
            title=node["title"],
            description=node.get("description"),
            state=node.get("state", {}).get("name", "Unknown"),
            priority=node.get("priority", 0),
            team_id=node.get("team", {}).get("id", ""),
            team_name=node.get("team", {}).get("name", "Unknown"),
            project_id=node.get("project", {}).get("id") if node.get("project") else None,
            project_name=node.get("project", {}).get("name") if node.get("project") else None,
            labels=[l["name"] for l in node.get("labels", {}).get("nodes", [])],
            url=node.get("url", ""),
        )
