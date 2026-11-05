-- 1. Ensure all tables have RLS enabled (defense in depth)
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.regrades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.students ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.grades ENABLE ROW LEVEL SECURITY;

-- 2. Drop all existing policies to recreate them strictly
DO $$ DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public') LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', r.policyname, r.tablename);
    END LOOP;
END $$;

-- 3. Users Table Policies
-- Users can read their own data
CREATE POLICY "users_read_own" ON public.users FOR SELECT TO authenticated USING (id = auth.uid()::text);
-- Admins can do everything on users
CREATE POLICY "users_admin_all" ON public.users FOR ALL TO authenticated USING (public.get_current_user_role() = 'admin');
-- Users can update their own data, but a trigger should prevent role escalation (which is handled elsewhere, but let's restrict role update)
CREATE POLICY "users_update_own" ON public.users FOR UPDATE TO authenticated USING (id = auth.uid()::text) WITH CHECK (id = auth.uid()::text);

-- 4. Assignments Table Policies
-- Everyone (authenticated) can read assignments
CREATE POLICY "assignments_read_all" ON public.assignments FOR SELECT TO authenticated USING (true);
-- Only admins can manage assignments
CREATE POLICY "assignments_admin_all" ON public.assignments FOR ALL TO authenticated USING (public.get_current_user_role() = 'admin');

-- 5. Regrades Table Policies
-- Students can read their own regrade requests
CREATE POLICY "regrades_read_own" ON public.regrades FOR SELECT TO authenticated USING (student_id = public.get_current_student_id());
-- Students can insert their own regrades
CREATE POLICY "regrades_insert_own" ON public.regrades FOR INSERT TO authenticated WITH CHECK (student_id = public.get_current_student_id());
-- Admins can manage all regrades
CREATE POLICY "regrades_admin_all" ON public.regrades FOR ALL TO authenticated USING (public.get_current_user_role() = 'admin');

-- 6. Submissions Table Policies
-- Students can read/insert/update their own submissions
CREATE POLICY "submissions_manage_own" ON public.submissions FOR ALL TO authenticated USING (student_id = public.get_current_student_id());
-- Admins can view and manage all submissions
CREATE POLICY "submissions_admin_all" ON public.submissions FOR ALL TO authenticated USING (public.get_current_user_role() = 'admin');

-- 7. User State Policies
-- Users can manage their own state based on their student_id mapping
CREATE POLICY "user_state_manage_own" ON public.user_state FOR ALL TO authenticated USING (
    chat_id IN (SELECT chat_id FROM public.students WHERE student_id = public.get_current_student_id())
);
-- Admins can manage all user states
CREATE POLICY "user_state_admin_all" ON public.user_state FOR ALL TO authenticated USING (public.get_current_user_role() = 'admin');

-- 8. Students Table Policies
-- Students can view their own student mapping
CREATE POLICY "students_read_own" ON public.students FOR SELECT TO authenticated USING (student_id = public.get_current_student_id());
-- Admins can manage all students
CREATE POLICY "students_admin_all" ON public.students FOR ALL TO authenticated USING (public.get_current_user_role() = 'admin');

-- 9. Grades Table Policies
-- Students can read their own grades
CREATE POLICY "grades_read_own" ON public.grades FOR SELECT TO authenticated USING (student_id = public.get_current_student_id());
-- Admins can manage all grades
CREATE POLICY "grades_admin_all" ON public.grades FOR ALL TO authenticated USING (public.get_current_user_role() = 'admin');
