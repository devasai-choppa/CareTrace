# Draft billing dispute — not legal advice

This letter is a draft. It is not legal or medical advice.

Account: {{ account }}
Claim: {{ claim_id }}
Hospital file date: {{ file_date }}

{{ narrative }}

{% for finding in findings %}
## {{ finding.rule_id }}
Expected {{ finding.expected }}. Billed or allowed: {{ finding.actual }}.
Amount in question: ${{ "%.2f"|format(finding.impact) }}.
Citation: {{ finding.source_file_uris|join(", ") }} (row {{ finding.evidence_hashes|join(", ") }}).
{% endfor %}

Please send a corrected bill or a written explanation by {{ deadline }}.

Total amount in question: ${{ "%.2f"|format(total) }}.
