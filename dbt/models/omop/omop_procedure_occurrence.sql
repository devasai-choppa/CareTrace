select * from {{ source('bronze', 'procedure_occurrence') }}
