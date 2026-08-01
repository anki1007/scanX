# Agent Architecture

> **Three Layers. Three Different Responsibilities.**
>
> **Environment → Feedback → Workflow**

------------------------------------------------------------------------

# High-Level Architecture

``` text
┌──────────────────────────┐
│ 1. Agent Harness         │
│    (Environment)         │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ 2. Loop Engineering      │
│    (Feedback System)     │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│ 3. Graph Engineering     │
│    (Workflow Engine)     │
└──────────────────────────┘
```

## 1. Agent Harness Engineering

Builds the execution environment for AI agents.

### Components

-   Tools
-   APIs
-   Memory
-   File System
-   Permissions
-   Runtime

------------------------------------------------------------------------

## 2. Loop Engineering

Improves work quality through iterative execution.

``` text
Think
  ↓
Act
  ↓
Check
  ↓
Feedback
  ↓
Repeat
```

------------------------------------------------------------------------

## 3. Graph Engineering

Controls the workflow using connected nodes.

### Components

-   Nodes
-   Branches
-   Joins
-   Parallel Execution
-   State

------------------------------------------------------------------------

# Layer Relationship

``` text
Agent Harness
(Environment)
      ↓
Loop Engineering
(Feedback)
      ↓
Graph Engineering
(Workflow)
```

------------------------------------------------------------------------

# Responsibility Matrix

  Layer               Responsibility           Focus
  ------------------- ------------------------ ----------------
  Agent Harness       Execution Environment    Infrastructure
  Loop Engineering    Continuous Improvement   Quality
  Graph Engineering   Workflow Orchestration   Control Flow

------------------------------------------------------------------------

# Key Principle

-   Agent Harness builds the environment.
-   Loop Engineering improves execution.
-   Graph Engineering orchestrates the workflow.

Together:

``` text
Environment
     ↓
Feedback
     ↓
Workflow
```
