select * from {{ source('bronze', 'drug_exposure') }}
