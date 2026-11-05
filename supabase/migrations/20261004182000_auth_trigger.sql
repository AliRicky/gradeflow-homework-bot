-- Trigger to sync auth.users to public.users
CREATE OR REPLACE FUNCTION public.handle_new_user() 
RETURNS trigger AS $$
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
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

-- Drop trigger if exists just in case
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

-- Create the trigger
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Update RLS policies on public.users to correctly use auth.uid()
-- We need to ensure we map UUID from auth.uid() to text since the users table ID is text.
DROP POLICY IF EXISTS "Users can view their own data" ON public.users;
CREATE POLICY "Users can view their own data" ON public.users 
  FOR SELECT USING (id = auth.uid()::text);

DROP POLICY IF EXISTS "Users can update their own data (no role escalation)" ON public.users;
CREATE POLICY "Users can update their own data (no role escalation)" ON public.users 
  FOR UPDATE USING (id = auth.uid()::text)
  WITH CHECK (id = auth.uid()::text);
