select * from {{ source('bronze', 'condition_occurrence') }}
