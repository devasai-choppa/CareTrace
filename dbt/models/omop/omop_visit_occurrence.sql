select * from {{ source('bronze', 'visit_occurrence') }}
