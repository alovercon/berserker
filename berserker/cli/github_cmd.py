"""
GitHub CLI commands for berserker.

Provides commands for GitHub integration:
- github install: Save GitHub Personal Access Token
- github run: Clone a repository
- github pr list: List pull requests
- github pr show: Show pull request details
- github pr diff: Show pull request diff
"""

import json
import os
import sys
import urllib.request
import urllib.error
from berserker.paths import get_config_dir


def _get_auth_file_path():
    # type: () -> str
    """Return the path to the auth.json file."""
    config_dir = get_config_dir()
    return os.path.join(config_dir, "auth.json")


def _load_auth_data():
    # type: () -> dict
    """Load authentication data from auth.json file."""
    auth_file = _get_auth_file_path()
    if os.path.exists(auth_file):
        with open(auth_file, "r") as f:
            try:
                return json.load(f)
            except (ValueError, IOError):
                return {}
    return {}


def _save_auth_data(data):
    # type: (dict) -> None
    """Save authentication data to auth.json file."""
    auth_file = _get_auth_file_path()
    config_dir = os.path.dirname(auth_file)
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)

    with open(auth_file, "w") as f:
        json.dump(data, f, indent=2)


def _get_github_token():
    # type: () -> str | None
    """Get GitHub token from auth.json or GITHUB_TOKEN environment variable."""
    auth_data = _load_auth_data()
    token = auth_data.get("github")
    if not token:
        token = os.environ.get("GITHUB_TOKEN")
    return token if token else None


def _make_github_request(url, token=None, headers=None):
    # type: (str, str | None, dict | None) -> dict
    """Make a GitHub API request and return JSON response."""
    if headers is None:
        headers = {}

    # Set default headers
    req_headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "berserker"}
    req_headers.update(headers)

    # Add authorization if token is provided
    if token:
        req_headers["Authorization"] = "token {}".format(token)

    request = urllib.request.Request(url, headers=req_headers)

    try:
        response = urllib.request.urlopen(request)
        return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Exception("Repository/PR not found")
        elif e.code == 401:
            raise Exception("Invalid token")
        else:
            raise Exception("GitHub API error: {}".format(e.code))
    except urllib.error.URLError as e:
        raise Exception("Network error: {}".format(str(e)))


def _parse_repo(repo):
    # type: (str) -> tuple
    """Parse repository string into owner and repo name."""
    # Handle URLs like https://github.com/owner/repo
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/") :]
    elif repo.startswith("http://github.com/"):
        repo = repo[len("http://github.com/") :]
    elif repo.startswith("git@github.com:"):
        repo = repo[len("git@github.com:") :]
        if repo.endswith(".git"):
            repo = repo[:-4]

    # Remove trailing .git if present
    if repo.endswith(".git"):
        repo = repo[:-4]

    parts = repo.split("/")
    if len(parts) != 2:
        raise Exception("Invalid repository format. Expected 'owner/repo'")

    return parts[0], parts[1]


def cmd_github_install(token):
    # type: (str) -> None
    """
    Save GitHub Personal Access Token to auth.json.

    Args:
        token: GitHub Personal Access Token
    """
    auth_data = _load_auth_data()
    auth_data["github"] = token
    _save_auth_data(auth_data)
    print("Saved GitHub Personal Access Token")


def cmd_github_run(repo):
    # type: (str) -> None
    """
    Clone a GitHub repository.

    Args:
        repo: Repository identifier (owner/repo or full URL)
    """
    # Parse repository
    owner, repo_name = _parse_repo(repo)
    full_repo = "{}/{}".format(owner, repo_name)

    # Clone the repository using git
    clone_url = "https://github.com/{}.git".format(full_repo)
    os.system("git clone {}".format(clone_url))
    print("Cloned {}. Use `python -m berserker run` to start.".format(full_repo))


def cmd_github_pr_list(repo):
    # type: (str) -> None
    """
    List pull requests for a repository.

    Args:
        repo: Repository identifier (owner/repo or full URL)
    """
    owner, repo_name = _parse_repo(repo)
    url = "https://api.github.com/repos/{}/{}/pulls".format(owner, repo_name)

    token = _get_github_token()
    try:
        prs = _make_github_request(url, token)

        if not prs:
            print("No pull requests found")
            return

        # Print header
        print("{:<8} | {:<50} | {:<10}".format("NUMBER", "TITLE", "STATE"))
        print("-" * 80)

        for pr in prs:
            number = str(pr["number"])
            title = pr["title"]
            if len(title) > 50:
                title = title[:47] + "..."
            state = pr["state"]
            print("{:<8} | {:<50} | {:<10}".format(number, title, state))

    except Exception as e:
        print("Error: {}".format(str(e)))
        sys.exit(1)


def cmd_github_pr_show(repo, number):
    # type: (str, int) -> None
    """
    Show pull request details.

    Args:
        repo: Repository identifier (owner/repo or full URL)
        number: Pull request number
    """
    owner, repo_name = _parse_repo(repo)
    url = "https://api.github.com/repos/{}/{}/pulls/{}".format(owner, repo_name, number)

    token = _get_github_token()
    try:
        pr = _make_github_request(url, token)

        print("Pull Request #{}".format(pr["number"]))
        print("Title: {}".format(pr["title"]))
        print("State: {}".format(pr["state"]))
        print("Author: {}".format(pr["user"]["login"]))
        print("")
        print("Body:")
        print(pr["body"] if pr["body"] else "(no description)")

    except Exception as e:
        print("Error: {}".format(str(e)))
        sys.exit(1)


def cmd_github_pr_diff(repo, number):
    # type: (str, int) -> None
    """
    Show pull request diff.

    Args:
        repo: Repository identifier (owner/repo or full URL)
        number: Pull request number
    """
    owner, repo_name = _parse_repo(repo)
    url = "https://api.github.com/repos/{}/{}/pulls/{}".format(owner, repo_name, number)

    token = _get_github_token()
    try:
        # Request diff format
        headers = {"Accept": "application/vnd.github.v3.diff"}
        request = urllib.request.Request(url, headers=headers)

        if token:
            request.add_header("Authorization", "token {}".format(token))

        response = urllib.request.urlopen(request)
        diff_content = response.read().decode("utf-8")
        print(diff_content)

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("Error: Repository/PR not found")
        elif e.code == 401:
            print("Error: Invalid token")
        else:
            print("Error: GitHub API error: {}".format(e.code))
        sys.exit(1)
    except urllib.error.URLError as e:
        print("Error: Network error: {}".format(str(e)))
        sys.exit(1)
