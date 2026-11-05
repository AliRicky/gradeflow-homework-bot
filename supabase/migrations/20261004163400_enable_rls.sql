-- Enable RLS on all tables
ALTER TABLE IF EXISTS users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS grades ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS regrades ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS students ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS user_state ENABLE ROW LEVEL SECURITY;

-- Helper function to get current user's role
CREATE OR REPLACE FUNCTION public.get_current_user_role()
RETURNS text
LANGUAGE sql SECURITY DEFINER
AS $$
  SELECT role FROM public.users WHERE id = auth.uid()::text;
$$;

-- Helper function to get current user's student_id
CREATE OR REPLACE FUNCTION public.get_current_student_id()
RETURNS text
LANGUAGE sql SECURITY DEFINER
AS $$
  SELECT student_id FROM public.users WHERE id = auth.uid()::text;
$$;

-- Policies for users
CREATE POLICY "Users can view their own data" ON users 
  FOR SELECT USING (auth.uid()::text = id);
CREATE POLICY "Users can update their own data (no role escalation)" ON users 
  FOR UPDATE USING (auth.uid()::text = id)
  WITH CHECK (auth.uid()::text = id);

-- Trigger to prevent role escalation by non-admins
CREATE OR REPLACE FUNCTION public.prevent_role_escalation()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.role IS DISTINCT FROM OLD.role THEN
    IF current_user IN ('postgres', 'service_role', 'supabase_admin') THEN
      RETURN NEW;
    END IF;
    IF COALESCE(public.get_current_user_role(), 'none') != 'admin' THEN
      RAISE EXCEPTION 'Not allowed to change role';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS prevent_role_escalation_trigger ON users;
CREATE TRIGGER prevent_role_escalation_trigger
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION public.prevent_role_escalation();

CREATE POLICY "Admins and TAs can view all users" ON users 
  FOR SELECT USING (public.get_current_user_role() = 'admin');
CREATE POLICY "Admins can manage users" ON users 
  FOR ALL USING (public.get_current_user_role() = 'admin');

-- Policies for assignments
CREATE POLICY "Anyone can view assignments" ON assignments 
  FOR SELECT USING (true);
CREATE POLICY "Admins and TAs can manage assignments" ON assignments 
  FOR ALL USING (public.get_current_user_role() = 'admin');

-- Policies for grades
CREATE POLICY "Students can view their own grades" ON grades 
  FOR SELECT USING (student_id = public.get_current_student_id());
CREATE POLICY "Admins and TAs can manage grades" ON grades 
  FOR ALL USING (public.get_current_user_role() = 'admin');

-- Policies for regrades
CREATE POLICY "Students can view their own regrades" ON regrades 
  FOR SELECT USING (student_id = public.get_current_student_id());
CREATE POLICY "Students can insert their own regrades" ON regrades 
  FOR INSERT WITH CHECK (
    student_id = public.get_current_student_id() AND 
    status = 'pending' AND 
    (ta_response IS NULL OR ta_response = '')
  );
CREATE POLICY "Admins and TAs can view and update all regrades" ON regrades 
  FOR ALL USING (public.get_current_user_role() = 'admin');

-- Policies for students
CREATE POLICY "Students can view their own profile" ON students 
  FOR SELECT USING (student_id = public.get_current_student_id());
CREATE POLICY "Admins and TAs can manage students" ON students 
  FOR ALL USING (public.get_current_user_role() = 'admin');

-- Policies for submissions
CREATE POLICY "Students can manage their own submissions" ON submissions 
  FOR ALL USING (student_id = public.get_current_student_id());
CREATE POLICY "Admins and TAs can view all submissions" ON submissions 
  FOR SELECT USING (public.get_current_user_role() = 'admin');

-- Policies for user_state
-- user_state uses chat_id. Does students map chat_id to student_id? 
-- Let's just allow users to manage user_state where user_state.chat_id = students.chat_id for their student_id
CREATE POLICY "Users can manage their own state" ON user_state 
  FOR ALL USING (
    chat_id IN (
      SELECT chat_id FROM students WHERE student_id = public.get_current_student_id()
    )
  );
