"""
AGENTS.md hierarchical instruction loading system for berserker.

Provides functions to find, load, and manage hierarchical AGENTS.md files
with circular reference prevention, deduplication, and token efficiency.

Python 3.8.10 compatible: uses os.path instead of pathlib, type comments,
Optional/Union types, and no match/case statements.
"""

from __future__ import annotations

import os
import time
from typing import List, Dict, Optional, Set, Tuple

# Cache for instruction files to avoid reloading unchanged files
_INSTRUCTION_CACHE = {}  # type: Dict[str, Tuple[float, str]]


def find_project_root(start_path):
    # type: (str) -> Optional[str]
    """Find the project root directory by looking for common markers.
    
    Args:
        start_path: Starting directory path to search from.
        
    Returns:
        Project root directory path or None if not found.
    """
    current_path = os.path.abspath(start_path)
    visited = set()  # type: Set[str]
    
    while current_path not in visited:
        # Check for common project root markers
        markers = ['.git', 'pyproject.toml', 'setup.py', 'package.json', '.gitignore']
        for marker in markers:
            marker_path = os.path.join(current_path, marker)
            if os.path.exists(marker_path):
                return current_path
                
        # Move up one directory
        parent_path = os.path.dirname(current_path)
        if parent_path == current_path:
            # Reached filesystem root
            break
            
        visited.add(current_path)
        current_path = parent_path
        
    return None


def find_nearest_agents_md(start_dir, max_depth=10):
    # type: (str, int) -> List[str]
    """Find AGENTS.md files by searching upward from start_dir.
    
    Prevents circular references using visited path tracking and respects
    depth limits to avoid runaway filesystem walks.
    
    Args:
        start_dir: Starting directory to search from.
        max_depth: Maximum number of directories to traverse upward.
        
    Returns:
        List of AGENTS.md file paths found, ordered from nearest to furthest.
    """
    agents_files = []  # type: List[str]
    current_dir = os.path.abspath(start_dir)
    visited_dirs = set()  # type: Set[str]
    depth = 0
    
    while depth < max_depth and current_dir not in visited_dirs:
        agents_path = os.path.join(current_dir, 'AGENTS.md')
        if os.path.isfile(agents_path):
            agents_files.append(agents_path)
            
        # Check if we've reached the project root
        project_root = find_project_root(current_dir)
        if project_root and current_dir == project_root:
            # Stop at project root to avoid going too far up
            break
            
        # Move up one directory
        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            # Reached filesystem root
            break
            
        visited_dirs.add(current_dir)
        current_dir = parent_dir
        depth += 1
        
    return agents_files


def _get_file_mtime(filepath):
    # type: (str) -> float
    """Get the modification time of a file.
    
    Args:
        filepath: Path to the file.
        
    Returns:
        File modification time as timestamp.
    """
    try:
        return os.path.getmtime(filepath)
    except (OSError, IOError):
        return 0.0


def _load_instruction_file(filepath):
    # type: (str) -> str
    """Load an instruction file with caching based on modification time.
    
    Args:
        filepath: Path to the AGENTS.md file.
        
    Returns:
        Content of the file as a string.
    """
    mtime = _get_file_mtime(filepath)
    
    # Check cache
    if filepath in _INSTRUCTION_CACHE:
        cached_mtime, cached_content = _INSTRUCTION_CACHE[filepath]
        if mtime == cached_mtime:
            return cached_content
            
    # Load file content
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (OSError, IOError, UnicodeDecodeError):
        content = ""
        
    # Update cache
    _INSTRUCTION_CACHE[filepath] = (mtime, content)
    return content


def load_hierarchical_instructions(start_dir, max_depth=10):
    # type: (str, int) -> List[Tuple[str, str]]
    """Load hierarchical instructions from AGENTS.md files.
    
    Finds AGENTS.md files by searching upward from start_dir and loads their
    contents with deduplication based on file modification times.
    
    Args:
        start_dir: Starting directory to search from.
        max_depth: Maximum number of directories to traverse upward.
        
    Returns:
        List of tuples (file_path, content) for each AGENTS.md file found,
        ordered from nearest to furthest from start_dir.
    """
    agents_files = find_nearest_agents_md(start_dir, max_depth)
    instructions = []  # type: List[Tuple[str, str]]
    
    for filepath in agents_files:
        content = _load_instruction_file(filepath)
        if content.strip():  # Only include non-empty files
            instructions.append((filepath, content))
            
    return instructions


def build_progressive_disclosure_context(
    start_dir, 
    task_keywords=None, 
    max_depth=10,
    max_total_chars=15000
):
    # type: (str, Optional[List[str]], int, int) -> str
    """Build context using progressive disclosure pattern for token efficiency.
    
    Loads hierarchical instructions and filters them based on task relevance
    to minimize token waste while providing necessary context.
    
    Args:
        start_dir: Starting directory to search from.
        task_keywords: Optional list of keywords to filter relevant instructions.
        max_depth: Maximum number of directories to traverse upward.
        max_total_chars: Maximum total characters to include in context.
        
    Returns:
        Combined context string with relevant instructions.
    """
    instructions = load_hierarchical_instructions(start_dir, max_depth)
    
    if not instructions:
        return ""
        
    # If no task keywords provided, return all instructions (within limit)
    if not task_keywords:
        combined = "\n\n".join(content for _, content in instructions)
        if len(combined) <= max_total_chars:
            return combined
        else:
            # Truncate to fit within limit
            return combined[:max_total_chars]
            
    # Filter instructions based on task keywords
    relevant_instructions = []
    total_chars = 0
    
    for filepath, content in instructions:
        # Check if any keyword appears in the content
        is_relevant = any(
            keyword.lower() in content.lower() 
            for keyword in task_keywords
        )
        
        if is_relevant:
            if total_chars + len(content) <= max_total_chars:
                relevant_instructions.append(content)
                total_chars += len(content)
            else:
                # Add partial content to fill remaining space
                remaining_chars = max_total_chars - total_chars
                if remaining_chars > 0:
                    relevant_instructions.append(content[:remaining_chars])
                break
                
    return "\n\n".join(relevant_instructions)


# Example usage and testing functions
def _example_usage():
    # type: () -> None
    """Example usage of the AGENTS.md loading system."""
    current_dir = os.getcwd()
    
    # Find all AGENTS.md files
    agents_files = find_nearest_agents_md(current_dir)
    print("Found AGENTS.md files:", agents_files)
    
    # Load hierarchical instructions
    instructions = load_hierarchical_instructions(current_dir)
    print("Loaded {} instruction files".format(len(instructions)))
    
    # Build context for a specific task
    context = build_progressive_disclosure_context(
        current_dir, 
        task_keywords=["python", "testing", "documentation"]
    )
    print("Context length:", len(context))
    print("Context preview:", context[:200] + "..." if len(context) > 200 else context)


if __name__ == "__main__":
    _example_usage()
