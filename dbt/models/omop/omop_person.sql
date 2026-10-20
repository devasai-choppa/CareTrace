select * from {{ source('bronze', 'person') }}
