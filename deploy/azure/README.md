# Azure Container Apps

```bash
az group create -n stocklens-rg -l eastus
az acr create -n <registry> -g stocklens-rg --sku Basic
az acr build -r <registry> -t stocklens:1.0.0 .
az keyvault create -n <vault> -g stocklens-rg
az keyvault secret set --vault-name <vault> -n stocklens-api-key --value "$(openssl rand -hex 32)"

az containerapp env create -n stocklens-env -g stocklens-rg -l eastus
az containerapp create -n stocklens -g stocklens-rg --environment stocklens-env \
  --image <registry>.azurecr.io/stocklens:1.0.0 --registry-server <registry>.azurecr.io \
  --target-port 8080 --ingress external --min-replicas 1 --max-replicas 4 \
  --cpu 0.5 --memory 1Gi --system-assigned \
  --env-vars STOCKLENS_TRUST_PROXY_HEADERS=true FORWARDED_ALLOW_IPS=* \
             STOCKLENS_API_KEY=secretref:api-key \
  --secrets "api-key=keyvaultref:https://<vault>.vault.azure.net/secrets/stocklens-api-key,identityref:system"
```

Put Azure Front Door with a WAF policy (Microsoft Default Rule Set + a rate-limit
custom rule) in front, and restrict the Container App's ingress to Front Door's
service tag. Use Azure Database for PostgreSQL Flexible Server with private access.
