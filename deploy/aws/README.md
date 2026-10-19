# AWS

Two supported shapes. Both keep secrets in AWS Secrets Manager and the database
in a private subnet.

## App Runner (simplest)

```bash
aws ecr create-repository --repository-name stocklens
docker build -t "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/stocklens:1.0.0" .
aws ecr get-login-password | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
docker push "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/stocklens:1.0.0"
aws apprunner create-service --cli-input-json file://deploy/aws/apprunner.json
```

Attach an AWS WAF web ACL to the service with the `AWSManagedRulesCommonRuleSet`,
`AWSManagedRulesKnownBadInputsRuleSet` and a rate-based rule (e.g. 300 requests
per 5 minutes per IP, 20 for `/api/v1/symbols/*/analyst`).

## ECS Fargate behind an ALB

`ecs-task-definition.json` runs the container read-only, non-root, with all
Linux capabilities dropped. Security groups:

| Group | Inbound | Outbound |
|---|---|---|
| `alb-sg` | 443 from `0.0.0.0/0` | 8080 to `app-sg` |
| `app-sg` | 8080 from `alb-sg` only | 443 to `0.0.0.0/0` (via NAT), 5432 to `db-sg` |
| `db-sg` | 5432 from `app-sg` only | none |

Tasks run in private subnets with no public IP; outbound HTTPS leaves through a
NAT gateway (add AWS Network Firewall with an FQDN allowlist for strict egress).
