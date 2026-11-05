CREATE OR REPLACE FUNCTION public.get_current_user_role()
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  RETURN (SELECT role FROM public.users WHERE id = auth.uid()::text LIMIT 1);
END;
$$;

CREATE OR REPLACE FUNCTION public.get_current_student_id()
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  RETURN (SELECT student_id::text FROM public.users WHERE id = auth.uid()::text LIMIT 1);
END;
$$;
