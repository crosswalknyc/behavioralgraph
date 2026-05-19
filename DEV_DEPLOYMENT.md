# Development & Production Deployment Guide

This guide explains how to use the separate development and production environments for Behavioral Graph.

## Overview

- **Production Site**: https://behavioralgraph.onrender.com
- **Development Site**: https://behavioral-graph-dev.onrender.com (after setup)

The dev site is restricted to **admin** and **super_admin** users only. Regular users will see an access denied page.

---

## Setting Up the Dev Environment on Render

### 1. Create a New Blueprint

1. Go to [Render Dashboard](https://dashboard.render.com)
2. Click **New** → **Blueprint**
3. Connect to your GitHub repository (`crosswalknyc/behavioralgraph`)
4. Set the **Blueprint file path** to: `render-dev.yaml`
5. Click **Apply**

### 2. Configure Environment Variables

After the service is created, go to the service settings and add these secrets:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | A unique secret key (different from production!) |
| `AWS_ACCESS_KEY_ID` | AWS credentials for S3 |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials for S3 |
| `SNOWFLAKE_USER` | Snowflake username |
| `SNOWFLAKE_PASSWORD` | Snowflake password |
| `SNOWFLAKE_ACCOUNT` | Snowflake account |
| `SNOWFLAKE_TOKEN` | Snowflake token (if using) |
| `GOOGLE_CLIENT_ID` | Gmail OAuth (optional) |
| `GOOGLE_CLIENT_SECRET` | Gmail OAuth (optional) |

### 3. Update APP_URL

After deployment, update the `APP_URL` environment variable to match your actual dev URL.

---

## Git Workflow

### Branch Strategy

```
main (production)
  └── dev (development)
```

- **`main`** branch → Deploys to production
- **`dev`** branch → Deploys to development

### Daily Development Workflow

1. **Work on the dev branch**:
   ```bash
   git checkout dev
   # Make your changes
   git add .
   git commit -m "Description of changes"
   git push origin dev
   ```

2. **Test on dev site**:
   - Visit https://behavioral-graph-dev.onrender.com
   - Log in with admin credentials
   - Test your changes thoroughly

3. **When ready for production**:
   ```bash
   # Switch to main
   git checkout main
   
   # Merge dev into main
   git merge dev
   
   # Push to production
   git push origin main
   ```

### Creating the Dev Branch (First Time Only)

```bash
# Create and push the dev branch
git checkout -b dev
git push -u origin dev
```

---

## How It Works

### Environment Detection

The app detects which environment it's running in via the `APP_ENV` environment variable:

- `APP_ENV=development` → Dev mode (restricted access)
- `APP_ENV=production` → Production mode (normal access)

### Access Control

In development mode:
- Only **admin** and **super_admin** users can access the site
- Regular users see an "Access Restricted" page with a link to the live site
- Health check endpoints (`/health`, `/healthz`, `/ready`) are always accessible

### Visual Indicator

When on the dev site, admins see a **red banner** at the top of the page:
```
🚧 DEVELOPMENT ENVIRONMENT - Changes here won't affect the live site.
```

---

## Render Service Configuration

### Production Service (`render.yaml`)
- Name: `behavioral-graph`
- URL: https://behavioralgraph.onrender.com
- Includes cron job for scheduled exports

### Development Service (`render-dev.yaml`)
- Name: `behavioral-graph-dev`
- URL: https://behavioral-graph-dev.onrender.com
- No cron job (to avoid duplicate automated emails)
- `FLASK_DEBUG=true` for better error messages

---

## Troubleshooting

### "Access Restricted" on Dev Site
- Make sure you're logged in as an admin or super_admin user
- Check that your user role is correctly set in the users database

### Changes Not Appearing
- Check that you pushed to the correct branch
- Verify the Render deployment completed successfully
- Clear your browser cache

### Dev Site Not Deploying
- Check Render dashboard for deployment errors
- Verify all required environment variables are set
- Check the build logs for any issues

---

## Best Practices

1. **Always test on dev first** before pushing to production
2. **Use meaningful commit messages** so you can track what changed
3. **Don't skip the dev step** for "small" changes - they can still break things
4. **Keep dev and main in sync** - merge frequently to avoid conflicts
5. **Monitor the dev site** after deploying to catch issues early

---

## Quick Reference

| Action | Command |
|--------|---------|
| Switch to dev | `git checkout dev` |
| Push to dev | `git push origin dev` |
| Switch to main | `git checkout main` |
| Merge dev to main | `git merge dev` |
| Push to production | `git push origin main` |
| Check current branch | `git branch` |
| See all branches | `git branch -a` |
