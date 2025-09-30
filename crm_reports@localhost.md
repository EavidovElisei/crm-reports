classDiagram
direction BT
class auth_tokens {
   varchar(50) service
   varchar(100) login
   varchar(255) password
   text current_token
   timestamp token_updated_at
   timestamp created_at
   integer id
}
class requests {
   timestamp created_at
   jsonb data
   timestamp updated_at
   bigint id
}

