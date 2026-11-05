-- 1. Ensure 'users' table aligns with V6 stabilized condition.
-- Drop legacy email column
ALTER TABLE public.users DROP COLUMN IF EXISTS email;

-- Rename 'name' to 'full_name' if it exists, to match the stabilized V6 schema
DO $$
BEGIN
  IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='name') THEN
    ALTER TABLE public.users RENAME COLUMN name TO full_name;
  END IF;
END $$;

-- Ensure other columns are text
ALTER TABLE public.users ALTER COLUMN role TYPE text;
ALTER TABLE public.users ALTER COLUMN full_name TYPE text;
ALTER TABLE public.users ALTER COLUMN student_id TYPE text;

-- 2. Fix get_current_student_id() type mismatch and avoid recursion
-- It MUST return text and query from public.users safely.
CREATE OR REPLACE FUNCTION public.get_current_student_id()
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
BEGIN
  -- Cast auth.uid() to text explicitly to avoid UUID type mismatch
  RETURN (SELECT student_id::text FROM public.users WHERE id = auth.uid()::text LIMIT 1);
END;
$func$;

-- Same fix for get_current_user_role() just in case
CREATE OR REPLACE FUNCTION public.get_current_user_role()
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $func$
BEGIN
  RETURN (SELECT role::text FROM public.users WHERE id = auth.uid()::text LIMIT 1);
END;
$func$;

-- 3. Verify and fix synchronization triggers
CREATE OR REPLACE FUNCTION public.handle_new_user() 
RETURNS trigger AS $func$
BEGIN
  INSERT INTO public.users (id, full_name, role, student_id, section)
  VALUES (
    NEW.id::text,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', 'New User'),
    COALESCE(NEW.raw_user_meta_data->>'role', 'student'),
    NEW.raw_user_meta_data->>'student_id',
    NEW.raw_user_meta_data->>'section'
  )
  ON CONFLICT (id) DO UPDATE SET
    full_name = EXCLUDED.full_name,
    role = EXCLUDED.role,
    student_id = EXCLUDED.student_id,
    section = EXCLUDED.section;
  RETURN NEW;
END;
$func$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Drop and recreate the trigger
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- 4. Enforce mandatory RLS
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.grades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.regrades ENABLE ROW LEVEL SECURITY;

-- 5. Strict Postgres access policies (Drafting missing policies or fixing them)
-- Avoid recursion in policies by not calling functions that query the table itself if possible,
-- or ensuring the functions use SECURITY DEFINER appropriately.
ALTER TABLE public.students ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_state ENABLE ROW LEVEL SECURITY;
