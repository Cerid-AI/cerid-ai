# Authentication — Token Lifetimes

The service issues JSON Web Tokens for authentication. **Access tokens expire
after 15 minutes**; **refresh tokens live for 30 days** and are rotated on
every use so a stolen refresh token is invalidated the moment the legitimate
client next refreshes. Tokens are signed with **RS256** using a key pair
whose public half is published at the JWKS endpoint.

Access tokens are stateless and never checked against a database on the hot
path; revocation before natural expiry relies on a short deny-list keyed by
token id. Sentinel fact: access tokens are valid for fifteen minutes and
refresh tokens for thirty days.
