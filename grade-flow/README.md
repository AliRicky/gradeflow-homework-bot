# Grade Flow — Frontend & API Application

The client-side single page application and serverless API functions for Grade Flow, built with React, TypeScript, Vite, Tailwind CSS, and Supabase.

## Architecture & Technology

- **Frontend Framework:** React 19 with TypeScript
- **Styling & Components:** Tailwind CSS, Framer Motion, Lucide React
- **Build Tooling:** Vite with Rollup code-splitting chunks
- **State & Sync:** React Context (`AuthContext`, `CourseContext`, `ThemeContext`) with Supabase real-time subscriptions
- **Backend / DB:** Supabase PostgreSQL with strict Row Level Security (RLS)
- **Serverless API:** Vercel serverless function (`api/manage-users.ts`) for administrative user provisioning

## Available Scripts

In the `app/` directory:

```bash
# Start local development server on port 3000
npm run dev

# Run Vitest automated test suite
npm run test

# Run strict TypeScript compiler verification
npm run typecheck

# Run linter
npm run lint

# Build optimized production bundle to dist/
npm run build

# Preview production build locally
npm run preview
```

## Directory Structure

```
app/
├── api/             # Vercel serverless API handlers (e.g. manage-users.ts)
├── public/          # Static assets (SVGs, icons)
├── src/
│   ├── components/  # Role-based UI components (admin/, student/, auth/, layout/)
│   ├── context/     # Global state providers (Auth, Course, Theme)
│   ├── services/    # Data layer & Supabase client integration
│   ├── types/       # Shared TypeScript interfaces & types
│   └── utils/       # Unit-tested business logic (grading, csv, crypto, pdfExport)
├── index.html       # Single-page app HTML entry point
├── package.json     # App dependencies and scripts
├── tsconfig.json    # TypeScript compiler configuration
├── vercel.json      # Vercel routing rules & rewrites
└── vite.config.ts   # Vite configuration with chunk splitting
```

## Documentation & Manuals

For comprehensive guides and administrative procedures, refer to the root `docs/` directory:
- [Admin & TA Manual](../docs/ADMIN_MANUAL.md)
- [Student Guide](../docs/STUDENT_MANUAL.md)
- [User Manual](../docs/USER_MANUAL.md)
- [OpenAPI Specification](../docs/openapi.json)
