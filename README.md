# Patch Studio

Patch Studio is a structured, safety-first desktop application for reviewing, validating, and applying unified diff patches to a local project directory.
It provides deterministic patch execution, preflight validation, and explicit operator control to ensure filesystem changes occur only after transparent review.
________________________________________

## Executive Summary

Patch Studio is designed for environments where:

-	Patch integrity matters
-	Filesystem safety is non-negotiable
- Blind or partial patch application is unacceptable
-	Deterministic outcomes are required
  
Unlike traditional command-line patch utilities that may silently skip hunks or partially apply changes, Patch Studio enforces a structured workflow:

1.	Normalize
2.	Parse
3.	Preflight
4.	Preview (dry-run)
5.	Apply (explicit confirmation)
   
This design ensures that all operations are visible, reversible, and root-bound.
________________________________________

### Core Capabilities

#### Unified Diff Support

-	Git-style diffs (diff --git)
-	index format diffs
-	Classic unified diffs
-	Automatic dialect detection
  
#### Supported Operations

-	File creation
-	File modification
-	File deletion
-	Rename (optionally gated)
-	Mode changes (optionally gated)
  
#### Deterministic Preview Engine

-	Full in-memory simulation prior to disk writes
-	Conflict detection before apply
-	Explicit blocking on unsafe conditions
-	No silent partial application
  
________________________________________
### Safety Architecture

Patch Studio enforces a strict execution model:

### Root-Bound Execution

All patch paths are resolved relative to a selected project root.
Operations targeting paths outside the root are blocked.

### Mandatory Preflight

Before preview or apply, the system validates:

-	Path existence
-	Path validity
-	Outside-root access attempts
-	Unsupported binary patches
-	Rename/delete/mode-change permissions
  
### Apply Gating

-	Apply is only enabled after a successful preview
-	Conflicted output is blocked unless explicitly allowed
-	Disk preflight is re-run prior to write
  
### Backup Strategy

All modified files are backed up under:
```bash
<project-root>/.patchstudio_backups/
```
Backups are timestamped and created before any modification.

### Atomic Writes
All file modifications use atomic write patterns to prevent partial corruption.
________________________________________
### Advanced Controls

Patch Studio includes configurable safeguards:

-	Strict filename matching
-	Best-effort fuzzy apply
-	Ignore whitespace differences
-	Preserve original line endings
-	Skip unsupported binary patches
-	Conflict marker mode:
    -	diff3
    -	merge    
-	Rename/delete/mode-change gating
-	Partial apply override (advanced use only)
-	Allow writing conflicted output (explicitly gated)
  
These controls allow adaptation to legacy codebases, large diffs, and heterogeneous development environments.
________________________________________
Operational Workflow
1.	Select project root
2.	Load or paste patch
3.	Run Preview (dry-run simulation)
4.	Review results and conflicts
5.	Apply changes
No disk modifications occur without explicit operator confirmation.
________________________________________
### Developer Utilities
Self-Test Mode
Patch Studio includes a built-in validation mode:
```bash
python -m patchstudio.app --selftest
```
This runs internal engine checks without launching the GUI.

### Modular Architecture
The refactored package structure separates:

-	Patch normalization
-	Unified diff parsing
-	Preflight validation
-	Apply engine
-	Diff generation
-	GUI presentation layer

This improves maintainability, auditability, and future extensibility.
________________________________________
### Installation
Requirements
-	Python 3.10+
-	PySide6

#### Install dependencies:
```bash
pip install PySide6
```
### Run Application

#### From project root:
```bash
python -m patchstudio.app
```
Or via entry script:
```bash
python run_patchstudio.py
```
________________________________________
### Intended Use Cases
-	Enterprise codebase patch review
-	Secure patch application workflows
-	Controlled environment updates
-	QA validation of patch integrity
-	Manual inspection before release integration
________________________________________
### Non-Goals
Patch Studio is not:
-	A Git client
-	A repository history manager
-	A 3-way merge tool
-	A replacement for CI/CD pipelines
It operates strictly on the current filesystem state.
________________________________________
### Design Principles
Patch Studio prioritizes:
-	Determinism over automation
-	Operator visibility over silent correction
-	Explicit gating over implicit behavior
-	Safety over speed
-	Reversibility over convenience
________________________________________
### Roadmap
Future enhancements may include:
-	Expanded test coverage
-	CLI-only execution mode
-	Patch signing / integrity verification
-	Enhanced conflict visualization
-	Structured logging export
________________________________________
###License

Specify your preferred license here.

