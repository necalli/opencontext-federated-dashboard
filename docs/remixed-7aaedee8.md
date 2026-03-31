# Building automated invoice validation with Claude's agentic capabilities

Claude's Skills, Projects, and Agent architecture provides a complete framework for automating vendor invoice validation—combining PDF extraction, policy rule encoding, multi-step orchestration, and intelligent error flagging. **Organizations deploying similar systems report 90%+ automation rates and processing time reductions from 15-20 minutes to under 3 minutes per invoice**, with approximately 10% of documents requiring human review. This guide covers the technical implementation details, architectural patterns, and best practices for building production-ready invoice validation workflows.

The recommended architecture uses an **Orchestrator-Worker pattern with parallel subagents**: a lead validation agent coordinates specialized workers (PDF extractor, policy validator, discrepancy flagger) that run concurrently, with results synthesized for approval routing. This pattern balances throughput with maintainability—Anthropic's engineering team reports that multi-agent architectures outperform single-agent approaches by **90%+ on breadth-focused tasks** like multi-document validation, while keeping each component independently testable.

## PDF extraction powers the foundation layer

Claude processes PDFs by simultaneously converting pages to images and extracting text, enabling analysis of both structured tables and visual layouts. The key capability for invoice validation is **native PDF support** with limits of 32MB file size and 100 pages per request—sufficient for most invoice packages.

Three methods enable PDF ingestion via the API:

```python
# Method 1: Base64 encoding for direct upload
import anthropic, base64

with open("invoice.pdf", "rb") as f:
    pdf_base64 = base64.standard_b64encode(f.read()).decode("utf-8")

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": pdf_base64
            }},
            {"type": "text", "text": "Extract invoice fields as structured JSON"}
        ]
    }]
)
```

For production workflows processing multiple documents, the **code execution sandbox** provides pre-installed PDF libraries including **pdfplumber** (excellent for table extraction), **PyMuPDF** (fast text extraction), and **pypdf** (form handling). The sandbox uses a secure containerized environment with Python 3.12.3, and containers persist across requests for stateful processing:

```python
# Enable code execution for PDF processing
response = client.beta.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=4096,
    headers={"anthropic-beta": "code-execution-2025-08-25"},
    tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
    container={"skills": [
        {"type": "anthropic", "skill_id": "pdf", "version": "latest"}
    ]},
    messages=[{"role": "user", "content": "Extract all tables from invoice.pdf"}]
)
```

The official **PDF Skill** from Anthropic handles text extraction, table parsing, form field detection, merging, and annotations. For invoices with varying formats, Claude's vision capabilities interpret layouts intelligently—Koncile.ai benchmarking shows **97% accuracy on text PDFs** with best-in-class JSON format consistency.

## Skills encode validation rules as modular capabilities

Agent Skills are filesystem-based modules that extend Claude's functionality through progressive disclosure—only loading detailed instructions when relevant to the task. A validation skill structure looks like:

```
invoice-validator/
├── SKILL.md              # Core instructions and metadata
├── scripts/
│   ├── validate_totals.py      # Deterministic calculation checks
│   ├── check_duplicates.py     # Invoice number matching
│   └── policy_rules.py         # Business rule evaluations
├── references/
│   ├── approved_vendors.json   # Master vendor list
│   └── expense_policies.md     # Policy documentation
└── templates/
    └── validation_report.json  # Output schema
```

The **SKILL.md** file defines when and how Claude uses the skill:

```yaml
---
name: invoice-validator
description: Validates construction invoices against schedules of values, checks 
  compliance with contract terms, flags discrepancies for review. Use when processing
  vendor pay applications or invoice submissions.
---

# Invoice Validation Skill

## Quick Start
1. Extract invoice line items using code execution + pdfplumber
2. Load schedule of values from references/schedule_of_values.json
3. Execute scripts/validate_totals.py for calculation verification
4. Cross-reference vendor against approved_vendors.json
5. Apply policy rules and generate validation report

## Validation Rules (Critical)
- All line items must match SOV categories
- Unit prices cannot exceed contracted rates by >5%
- Quantities within approved change order limits
- Retainage calculations accurate to $0.01
- Invoice date not future-dated

## Output Format
Return JSON with: extraction_results, validation_findings, severity_flags, 
recommended_action
```

For complex validation logic, **three patterns** work effectively:

**Pattern 1: Declarative Rules (best for business users)**
```yaml
# policy_rules.yaml
rules:
  - id: "VAL001"
    field: "total_amount"
    condition: "value <= 100000"
    severity: "critical"
    message: "Invoice exceeds single-approval threshold"
    
  - id: "VAL002"  
    type: "calculation"
    expression: "abs(total - sum(line_items.total)) < 0.01"
    severity: "critical"
    message: "Line items do not sum to invoice total"
```

**Pattern 2: Deterministic Scripts (best for calculations)**
```python
# scripts/validate_totals.py
def validate_invoice_math(invoice_data: dict) -> dict:
    findings = []
    
    # Line item calculation check
    for item in invoice_data["line_items"]:
        expected = item["quantity"] * item["unit_price"]
        if abs(item["total"] - expected) > 0.01:
            findings.append({
                "rule": "VAL003",
                "severity": "critical",
                "item": item["description"],
                "expected": expected,
                "actual": item["total"]
            })
    
    # Subtotal reconciliation
    line_sum = sum(item["total"] for item in invoice_data["line_items"])
    if abs(invoice_data["subtotal"] - line_sum) > 0.01:
        findings.append({
            "rule": "VAL004",
            "severity": "critical",
            "message": "Subtotal does not match line item sum"
        })
    
    return {"passed": len(findings) == 0, "findings": findings}
```

**Pattern 3: LLM Evaluation (best for judgment calls)**
```python
POLICY_VALIDATION_PROMPT = """
Apply these validation rules to the extracted invoice data:

CRITICAL RULES (block processing):
1. Invoice date must not be in the future
2. Vendor must be in approved vendor list: {approved_vendors}
3. All required backup documentation present: {required_docs}

MAJOR RULES (flag for review):
4. Invoice older than 90 days requires explanation
5. Amount variance >5% from SOV line item requires justification
6. First-time vendor requires W-9 verification

Return structured validation report with rule_id, status, severity, details.
"""
```

## Orchestrator-worker architecture handles multi-step validation

The recommended pattern deploys a **lead agent orchestrating specialized subagents**, with parallel execution for independent validations:

```
┌────────────────────────────────────────────────────────────────┐
│                  LEAD VALIDATION AGENT                          │
│  (Plans validation strategy, spawns workers, synthesizes)       │
└────────────────────────┬───────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │ PARALLEL      │               │ PARALLEL
         ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ PDF Extract │  │   Policy    │  │  Schedule   │
│  Subagent   │  │  Validator  │  │  Matcher    │
│             │  │  Subagent   │  │  Subagent   │
│ • Tables    │  │ • Rules     │  │ • SOV match │
│ • Line items│  │ • Thresholds│  │ • Variance  │
│ • Metadata  │  │ • Compliance│  │ • History   │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       └────────────────┼────────────────┘
                        ▼
              ┌─────────────────────┐
              │ DISCREPANCY FLAGGER │
              │   & APPROVAL ROUTER │
              └─────────────────────┘
```

Custom subagents are defined in markdown files stored at `~/.claude/agents/` (user-level) or `.claude/agents/` (project-level):

```yaml
---
name: policy-validator
description: Validates invoice against contract terms and company policies
model: sonnet
tools:
  - Read
  - Grep
  - Bash
---

You are an invoice policy compliance specialist. Your role is to:
1. Load policy rules from references/expense_policies.md
2. Load approved vendor list from references/approved_vendors.json
3. Validate the extracted invoice data against all applicable rules
4. Return structured findings with severity levels

## Communication Protocol
Return JSON with: {rules_checked, findings[], overall_status, requires_human_review}
```

Using the **Agent SDK**, the orchestration looks like:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async def validate_invoice_package(invoice_pdf, backup_docs):
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Glob", "Bash", "Task", "Skill"],
        permission_mode="acceptEdits",
        system_prompt=ORCHESTRATOR_PROMPT,
        mcp_servers={
            "accounting": quickbooks_server,
            "documents": gdrive_server
        }
    )
    
    async for message in query(
        prompt=f"""Process invoice validation:
        1. Extract data from {invoice_pdf}
        2. Verify backup documentation: {backup_docs}
        3. Validate against SOV and policies
        4. Generate discrepancy report with approval routing""",
        options=options
    ):
        if message.type == "result":
            return message.result
```

Key architectural benefits: **context isolation** (each subagent uses only relevant context), **parallel execution** (LangChain benchmarks show 67% fewer tokens vs. sequential), and **maintainable separation** (teams own specific validation domains).

## Projects maintain persistent context across sessions

Claude Projects serve as self-contained workspaces that store vendor agreements, schedules of values, and policy documents in a **persistent knowledge base**. The base context window of **200K tokens** (~500 pages) automatically expands with RAG retrieval when needed.

For invoice validation, configure the Project with:

**Knowledge Base Contents:**
- Master service agreements (PDF)
- Schedule of values by vendor/project (JSON or CSV)
- Expense policy handbook
- Approved vendor registry
- Previous validation results for pattern learning

**Project Instructions:**
```
When validating vendor invoices:
1. First retrieve the relevant schedule of values from the knowledge base
2. Extract all line items and match against SOV categories
3. Apply variance thresholds: 
   - <2% variance: auto-approve
   - 2-5% variance: flag for review
   - >5% variance: escalate to project manager
4. Check backup documentation requirements per contract terms
5. Log validation results for audit trail
```

The **Memory Tool** enables agents to store and retrieve information beyond the context window—particularly useful for tracking validation patterns across multiple invoices:

```python
# Context management configuration
context_management = {
    "edits": [{
        "type": "clear_tool_uses_20250919",
        "exclude_tools": ["memory"]  # Preserve accumulated findings
    }]
}
```

Anthropic reports this combination yields **84% reduction in token consumption** for long-running workflows while maintaining continuity.

## MCP integrations connect to enterprise systems

The Model Context Protocol enables standardized connections to external systems. Key integrations for invoice validation:

**PDF Processing:**
- **pdf-reader-mcp** (SylphxAI): 5-10x faster with parallel processing, 94%+ test coverage
- **AWS Document Loader MCP**: Multi-format support using pdfplumber

```json
{
  "mcpServers": {
    "pdf-reader": {
      "command": "npx",
      "args": ["@sylphx/pdf-reader-mcp"],
      "cwd": "/path/to/invoice-folder"
    }
  }
}
```

**Accounting Systems:**
- **CData QuickBooks/NetSuite MCP**: Real-time AR/AP queries, vendor validation
- **Oracle NetSuite AI Connector**: Native MCP integration with SuiteQL

**Document Management:**
- **Google Drive MCP**: File access, search, folder organization
- **SharePoint MCP**: Site discovery, document libraries, approval workflows

For custom integrations, build MCP servers using **FastMCP**:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("invoice-validator")

@mcp.tool()
async def validate_against_sov(invoice_data: dict, project_id: str) -> dict:
    """Validate invoice line items against schedule of values.
    
    Args:
        invoice_data: Extracted invoice with line items
        project_id: Project identifier for SOV lookup
    """
    sov = await load_schedule_of_values(project_id)
    variances = calculate_variances(invoice_data, sov)
    return {"variances": variances, "within_threshold": all_within_threshold(variances)}

@mcp.tool()
async def route_for_approval(invoice_id: str, findings: list) -> str:
    """Route invoice based on validation findings and amount thresholds."""
    # Approval routing logic
    return routing_result
```

## Error flagging distinguishes severity for smart routing

A three-tier severity framework enables appropriate escalation:

| Severity | Action | Examples |
|----------|--------|----------|
| **Critical** | Auto-reject, immediate alert | Future-dated invoice, missing vendor, calculation failures |
| **Major** | Queue for human review | Amount threshold exceeded, >5% SOV variance, unknown vendor |
| **Minor** | Log and continue | Missing optional fields, formatting issues |

The escalation engine evaluates findings and routes accordingly:

```python
class EscalationEngine:
    def determine_action(self, invoice, validation_results):
        critical = [r for r in validation_results 
                   if not r['passed'] and r['severity'] == 'critical']
        major = [r for r in validation_results 
                if not r['passed'] and r['severity'] == 'major']
        
        if critical:
            return {"action": "auto_reject", "reason": critical}
        
        if major:
            return {"action": "human_review", "reviewer": "ap_clerk", 
                   "reason": major}
        
        if invoice.total > self.thresholds['manager_approval']:
            return {"action": "manager_review", 
                   "reason": "Amount exceeds approval threshold"}
        
        if invoice.extraction_confidence < 0.8:
            return {"action": "human_review",
                   "reason": "Low extraction confidence"}
        
        return {"action": "auto_approve"}
```

**Confidence scoring** adds another dimension—when Claude extracts fields, track confidence per field and flag documents where weighted confidence falls below **80%** for human verification:

```python
field_weights = {
    "invoice_number": 0.20,
    "vendor_name": 0.20,
    "total_amount": 0.25,
    "invoice_date": 0.15,
    "line_items": 0.20
}

def calculate_confidence(extraction):
    return sum(extraction.get(f"{field}_confidence", 0.5) * weight 
               for field, weight in field_weights.items())
```

## Handling varying vendor formats requires adaptive extraction

Different vendors use different layouts, field names, and structures. The solution combines **template-free LLM extraction** with **schema enforcement**:

```python
ADAPTIVE_PROMPT = """
Extract invoice data from this document. Interpret terminology intelligently:
- "Invoice #", "Bill No.", "Reference" → invoice_number
- "Vendor", "Supplier", "Sold By" → vendor_name
- "Grand Total", "Amount Due", "Balance" → total_amount

Handle format variations:
- Dates: Accept MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD, written dates
- Currency: Extract symbol/code, normalize to 3-letter ISO
- Amounts: Handle commas/periods as decimal separators

Return standardized JSON matching the schema regardless of source format.
"""
```

Enforce output structure with **Pydantic**:

```python
from pydantic import BaseModel, validator
from typing import List, Optional

class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    total: float
    
    @validator('total')
    def validate_total(cls, v, values):
        expected = values.get('quantity', 0) * values.get('unit_price', 0)
        if abs(v - expected) > 0.01:
            raise ValueError('Line item total mismatch')
        return v

class Invoice(BaseModel):
    invoice_number: str
    invoice_date: date
    vendor_name: str
    line_items: List[LineItem]
    subtotal: float
    tax_amount: Optional[float]
    total_amount: float
```

For high-volume processing with known vendors, maintain a **template library** that accelerates extraction and improves accuracy.

## Real-world implementations demonstrate proven patterns

**Holcim** (global building materials) processes ~3,900 emails monthly containing 2,000+ invoices using Claude on AWS Bedrock. Their "Omnivore" system achieves **95% automated processing** with only 10% requiring manual review—removing 90% of manual work while improving quality. Architecture: AWS Lambda + Bedrock + Claude.

**REVA Air Ambulance** reduced AP processing from 15-20 minutes to under 3 minutes per invoice—an **80%+ reduction**—using AI-powered extraction and validation.

Key success patterns from these implementations:
- **Parallel validation**: Run AI alongside existing manual processes initially to calibrate
- **Exception design**: Plan for ~10% requiring human review from the start
- **Schema enforcement**: Define explicit output schemas and quality benchmarks
- **Phased rollout**: Foundation (weeks 1-4) → Optimization (weeks 5-12) → Scale (weeks 13+)

## Conclusion: Building blocks for production deployment

The complete invoice validation workflow combines these capabilities:

1. **PDF Skill + Code Execution** for document extraction with pdfplumber/PyMuPDF
2. **Custom Validation Skills** encoding policy rules as declarative YAML + deterministic scripts
3. **Orchestrator-Worker Agent Architecture** with parallel subagents for independent validations
4. **Projects** storing schedules of values, vendor agreements, and policy documents
5. **MCP Servers** connecting to accounting systems (QuickBooks/NetSuite) and document storage
6. **Three-tier escalation** routing critical/major/minor findings appropriately
7. **Pydantic schemas** enforcing consistent output regardless of vendor format variation

Start with the PDF Skill for extraction, add a custom validation skill with your policy rules, then expand to full agent orchestration as volume grows. The architecture scales from single-invoice validation to enterprise batch processing—Holcim's implementation demonstrates the pattern handles thousands of invoices monthly with minimal human intervention.

Open-source starting points include **Sparrow** (github.com/katanaml/sparrow) for document processing pipelines, **Unstract** (github.com/Zipstack/unstract) for no-code document intelligence, and the **Anthropic Cookbook** (github.com/anthropics/claude-cookbooks) for PDF extraction examples. The official Skills repository (github.com/anthropics/skills) provides production-ready document handling capabilities.