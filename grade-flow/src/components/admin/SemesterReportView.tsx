import React, { useState, useMemo } from 'react';
import {
  FileSpreadsheet,
  Download,
  Copy,
  Printer,
  Search,
  Sparkles,
  Calculator,
  Award,
  CheckCircle2,
  Sliders,
  Database
} from 'lucide-react';
import { useCourse } from '../../context/CourseContext';
import { useAuth } from '../../context/AuthContext';
import {
  calculateStudentAssignmentScore,
  calculateSemesterSummary,
  calculateAssignmentDenominator,
  roundUpToTwoDecimals
} from '../../utils/grading';
import { downloadCSV } from '../../utils/csv';
import {
  PROFESSOR_STUDENTS,
  PROFESSOR_ASSIGNMENTS,
  PROFESSOR_GRADES
} from '../../data/professorSemesterData';
import { Assignment, User } from '../../types';

export const SemesterReportView: React.FC = () => {
  const {
    assignments: dbAssignments,
    grades: dbGrades,
    courseConfig,
    updateCourseConfig,
  } = useCourse();
  const { users } = useAuth();

  const [activeTab, setActiveTab] = useState<string>('final-scores');
  const [searchQuery, setSearchQuery] = useState('');
  const [copiedNotification, setCopiedNotification] = useState(false);
  const [useProfessorDataset, setUseProfessorDataset] = useState<boolean>(() => {
    return dbAssignments.length === 0;
  });

  // Decide which dataset to view: current DB or Professor's 10-table dataset
  const activeAssignments: Assignment[] = useProfessorDataset ? PROFESSOR_ASSIGNMENTS : dbAssignments;
  const activeGrades = useProfessorDataset ? PROFESSOR_GRADES : dbGrades;
  const activeStudents: User[] = useProfessorDataset
    ? PROFESSOR_STUDENTS
    : users.filter(u => u.role === 'student');

  // Semester Scale & Weight Mode
  const semesterScale = courseConfig.semesterScale || 5.0;
  const weightMode = courseConfig.weightMode || 'equal';

  // Compute semester summary for all students
  const semesterSummary = useMemo(() => {
    return calculateSemesterSummary({
      assignments: activeAssignments,
      students: activeStudents,
      grades: activeGrades,
      semesterScale,
      weightMode,
    });
  }, [activeAssignments, activeStudents, activeGrades, semesterScale, weightMode]);

  // Filtered students by search
  const filteredStudents = useMemo(() => {
    if (!searchQuery.trim()) return semesterSummary;
    const q = searchQuery.toLowerCase();
    return semesterSummary.filter(
      s => s.studentName.toLowerCase().includes(q) || s.studentNumber.toLowerCase().includes(q)
    );
  }, [semesterSummary, searchQuery]);

  // Current assessment if activeTab is an assignment
  const currentAssignment = useMemo(() => {
    if (activeTab === 'final-scores') return null;
    return activeAssignments.find(a => a.id === activeTab || a.shortCode.toLowerCase() === activeTab.toLowerCase());
  }, [activeTab, activeAssignments]);

  // Copy table data to clipboard
  const handleCopyTable = () => {
    let tsv = '';
    if (activeTab === 'final-scores') {
      const header = ['Row', 'First Name', 'Last Name', 'Student ID', ...activeAssignments.map(a => a.shortCode), `Final Score (Scale ${semesterScale})`];
      tsv += header.join('\t') + '\n';
      filteredStudents.forEach((s, idx) => {
        const parts = s.studentName.split(' ');
        const row = [
          idx + 1,
          parts[0] || '',
          parts.slice(1).join(' ') || '',
          s.studentNumber,
          ...activeAssignments.map(a => (s.assignmentScores[a.id]?.displayScore ?? 0).toFixed(2)),
          s.displaySemesterTotal.toFixed(2),
        ];
        tsv += row.join('\t') + '\n';
      });
    } else if (currentAssignment) {
      const qCols = currentAssignment.questions.map(q => q.title);
      const header = ['Row', 'Student Name', 'Student ID', ...qCols, 'Sum Points', `Final Score (Scale ${currentAssignment.targetScale ?? 1.0})`];
      tsv += header.join('\t') + '\n';
      filteredStudents.forEach((s, idx) => {
        const g = activeGrades[`${s.studentId}_${currentAssignment.id}`];
        const calc = calculateStudentAssignmentScore(currentAssignment, g?.scores);
        const qScores = currentAssignment.questions.map(q => {
          const val = g?.scores?.[q.id];
          return val !== undefined && val !== null ? val : '0';
        });
        const row = [
          idx + 1,
          s.studentName,
          s.studentNumber,
          ...qScores,
          calc.totalEarned,
          calc.displayScore.toFixed(2),
        ];
        tsv += row.join('\t') + '\n';
      });
    }

    navigator.clipboard.writeText(tsv);
    setCopiedNotification(true);
    setTimeout(() => setCopiedNotification(false), 2500);
  };

  // Export current table to CSV
  const handleExportCSV = () => {
    if (activeTab === 'final-scores') {
      const header = ['"Name"','"Family Name"','"Student ID"',...activeAssignments.map(a => `"${a.shortCode}"`),`"Final Score (${semesterScale} pts)"`];
      const rows = [header.join(',')];
      filteredStudents.forEach(s => {
        const parts = s.studentName.split(' ');
        const row = [
          `"${parts[0] || ''}"`,
          `"${parts.slice(1).join(' ') || ''}"`,
          `"${s.studentNumber}"`,
          ...activeAssignments.map(a => `"${(s.assignmentScores[a.id]?.displayScore ?? 0).toFixed(2)}"`),
          `"${s.displaySemesterTotal.toFixed(2)}"`,
        ];
        rows.push(row.join(','));
      });
      downloadCSV(`Semester_Summary_Report_Scale_${semesterScale}.csv`, rows.join('\n'));
    } else if (currentAssignment) {
      const qCols = currentAssignment.questions.map(q => `"${q.title}"`);
      const header = ['"Student Name"','"Student ID"',...qCols,'"Total Points"',`"Final Score (${currentAssignment.targetScale ?? 1.0})"`];
      const rows = [header.join(',')];
      filteredStudents.forEach(s => {
        const g = activeGrades[`${s.studentId}_${currentAssignment.id}`];
        const calc = calculateStudentAssignmentScore(currentAssignment, g?.scores);
        const qScores = currentAssignment.questions.map(q => {
          const val = g?.scores?.[q.id];
          return val !== undefined && val !== null ? `"${val}"` : '"0"';
        });
        const row = [
          `"${s.studentName}"`,
          `"${s.studentNumber}"`,
          ...qScores,
          `"${calc.totalEarned}"`,
          `"${calc.displayScore.toFixed(2)}"`,
        ];
        rows.push(row.join(','));
      });
      downloadCSV(`${currentAssignment.shortCode}_Report.csv`, rows.join('\n'));
    }
  };

  // Export all 10 tables combined CSV
  const handleExportAll10Tables = () => {
    const sections: string[] = [];

    // Table 10: Semester Summary
    sections.push('=== TABLE 10: END-OF-SEMESTER SUMMARY MATRIX ===');
    const summaryHeader = ['"First Name"','"Last Name"','"Student ID"',...activeAssignments.map(a => `"${a.shortCode}"`),`"Semester Total (${semesterScale})"`];
    sections.push(summaryHeader.join(','));
    semesterSummary.forEach(s => {
      const parts = s.studentName.split(' ');
      sections.push([
        `"${parts[0] || ''}"`,
        `"${parts.slice(1).join(' ') || ''}"`,
        `"${s.studentNumber}"`,
        ...activeAssignments.map(a => `"${(s.assignmentScores[a.id]?.displayScore ?? 0).toFixed(2)}"`),
        `"${s.displaySemesterTotal.toFixed(2)}"`,
      ].join(','));
    });
    sections.push('\n');

    // Tables 1..9: Individual Assessments
    activeAssignments.forEach((asg, aIdx) => {
      sections.push(`=== TABLE ${aIdx + 1}: ${asg.shortCode} ASSESSMENT BREAKDOWN ===`);
      const qCols = asg.questions.map(q => `"${q.title}"`);
      sections.push(['"Student Name"','"Student ID"',...qCols,'"Sum Points"',`"Final Score (${asg.targetScale ?? 1.0})"`].join(','));
      semesterSummary.forEach(s => {
        const g = activeGrades[`${s.studentId}_${asg.id}`];
        const calc = calculateStudentAssignmentScore(asg, g?.scores);
        const qScores = asg.questions.map(q => {
          const val = g?.scores?.[q.id];
          return val !== undefined && val !== null ? `"${val}"` : '"0"';
        });
        sections.push([
          `"${s.studentName}"`,
          `"${s.studentNumber}"`,
          ...qScores,
          `"${calc.totalEarned}"`,
          `"${calc.displayScore.toFixed(2)}"`,
        ].join(','));
      });
      sections.push('\n');
    });

    downloadCSV('Complete_10_Table_Semester_Report.csv', sections.join('\n'));
  };

  // Calculate Averages for the Final Scores table
  const finalScoreAverages = useMemo(() => {
    if (filteredStudents.length === 0) return { asgAverages: {}, overallAverage: 0 };
    const asgSums: Record<string, number> = {};
    activeAssignments.forEach(a => { asgSums[a.id] = 0; });
    let totalSum = 0;

    filteredStudents.forEach(s => {
      activeAssignments.forEach(a => {
        asgSums[a.id] += s.assignmentScores[a.id]?.exactScore ?? 0;
      });
      totalSum += s.exactSemesterTotal;
    });

    const asgAverages: Record<string, string> = {};
    activeAssignments.forEach(a => {
      asgAverages[a.id] = roundUpToTwoDecimals(asgSums[a.id] / filteredStudents.length).toFixed(2);
    });
    const overallAverage = roundUpToTwoDecimals(totalSum / filteredStudents.length).toFixed(2);

    return { asgAverages, overallAverage };
  }, [filteredStudents, activeAssignments]);

  return (
    <div className="space-y-6">
      {/* Top Banner & Control Deck */}
      <div className="bg-white dark:bg-[#09090B] border border-zinc-200 dark:border-zinc-800 rounded-3xl p-6 shadow-sm">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-[#3B82F6]">
              <Award className="w-4 h-4" />
              <span>Official Semester Summary & Multi-Table Report</span>
            </div>
            <h1 className="text-2xl font-bold font-display text-zinc-900 dark:text-zinc-100">
              End-of-Semester Performance Matrix
            </h1>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-2xl">
              Proportional normalized grading engine with auto-calculated denominators, uncapped bonus overflow,
              and strict 2-decimal ceiling rounding (<code className="text-[#3B82F6]">ceil(x * 100) / 100</code>).
            </p>
          </div>

          {/* Quick Dataset Switcher Pill */}
          <div className="flex items-center gap-2 bg-zinc-100 dark:bg-zinc-900 p-1.5 rounded-2xl border border-zinc-200 dark:border-zinc-800 self-start lg:self-auto">
            <button
              onClick={() => setUseProfessorDataset(true)}
              className={`px-3 py-1.5 text-xs font-semibold rounded-xl transition-all cursor-pointer ${
                useProfessorDataset
                  ? 'bg-white dark:bg-zinc-800 text-[#3B82F6] shadow-xs'
                  : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
              }`}
            >
              Professor 10-Table Dataset ({PROFESSOR_STUDENTS.length} Students)
            </button>
            <button
              onClick={() => setUseProfessorDataset(false)}
              className={`px-3 py-1.5 text-xs font-semibold rounded-xl transition-all cursor-pointer ${
                !useProfessorDataset
                  ? 'bg-white dark:bg-zinc-800 text-[#3B82F6] shadow-xs'
                  : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
              }`}
            >
              Active Database ({dbAssignments.length} Assessments)
            </button>
          </div>
        </div>

        {/* Configuration Bar: Semester Scale & Weight Mode */}
        <div className="mt-6 pt-5 border-t border-zinc-100 dark:border-zinc-800/80 grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Target Score Scale Selector */}
          <div className="bg-zinc-50 dark:bg-zinc-900/50 p-3.5 rounded-2xl border border-zinc-200/60 dark:border-zinc-800">
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                <Sliders className="w-3.5 h-3.5 text-[#3B82F6]" />
                <span>Semester Target Scale</span>
              </label>
              <span className="text-[10px] text-zinc-400 font-mono">Nominal Max: {semesterScale}.00</span>
            </div>
            <div className="flex items-center gap-1.5">
              {[5.0, 6.0, 9.0, 20.0].map(s => (
                <button
                  key={s}
                  onClick={() => updateCourseConfig({ semesterScale: s })}
                  className={`flex-1 py-1 text-xs font-mono font-bold rounded-lg border transition-all cursor-pointer ${
                    semesterScale === s
                      ? 'bg-[#3B82F6] text-white border-[#3B82F6] shadow-xs'
                      : 'bg-white dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:border-[#3B82F6]/50'
                  }`}
                >
                  {s}
                </button>
              ))}
              <input
                type="number"
                step="0.5"
                min="1"
                max="100"
                value={semesterScale}
                onChange={e => updateCourseConfig({ semesterScale: parseFloat(e.target.value) || 5.0 })}
                className="w-16 px-2 py-1 text-xs font-mono font-bold text-center rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-zinc-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-[#3B82F6]"
                title="Custom scale value"
              />
            </div>
          </div>

          {/* Weighting Mode */}
          <div className="bg-zinc-50 dark:bg-zinc-900/50 p-3.5 rounded-2xl border border-zinc-200/60 dark:border-zinc-800">
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                <Calculator className="w-3.5 h-3.5 text-[#3B82F6]" />
                <span>Assignment Weighting</span>
              </label>
              <span className="text-[10px] text-zinc-400 font-mono">
                {weightMode === 'equal' ? '1/N equal ratio' : 'Syllabus weights'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => updateCourseConfig({ weightMode: 'equal' })}
                className={`flex-1 py-1.5 text-xs font-medium rounded-lg border transition-all cursor-pointer ${
                  weightMode === 'equal'
                    ? 'bg-[#3B82F6] text-white border-[#3B82F6]'
                    : 'bg-white dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300'
                }`}
              >
                Equal (1/{activeAssignments.length})
              </button>
              <button
                onClick={() => updateCourseConfig({ weightMode: 'weightPercent' })}
                className={`flex-1 py-1.5 text-xs font-medium rounded-lg border transition-all cursor-pointer ${
                  weightMode === 'weightPercent'
                    ? 'bg-[#3B82F6] text-white border-[#3B82F6]'
                    : 'bg-white dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300'
                }`}
              >
                Custom Weights (%)
              </button>
            </div>
          </div>

          {/* Rounding Cadence Info Pill */}
          <div className="bg-zinc-50 dark:bg-zinc-900/50 p-3.5 rounded-2xl border border-zinc-200/60 dark:border-zinc-800 flex flex-col justify-between">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                <span>Mathematical Cadence</span>
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-bold font-mono">
                Uncapped Active
              </span>
            </div>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-1 font-mono">
              Intermediate: Exact float | Display: ⌈x · 100⌉ / 100
            </p>
          </div>
        </div>
      </div>

      {/* 10-Table Tab Navigation & Action Hub */}
      <div className="bg-white dark:bg-[#09090B] border border-zinc-200 dark:border-zinc-800 rounded-3xl p-5 shadow-sm space-y-4">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          {/* Scrollable Tab Strip for All 10 Tables */}
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 no-scrollbar touch-pan-x">
            <button
              onClick={() => setActiveTab('final-scores')}
              className={`px-3.5 py-2 text-xs font-bold rounded-xl whitespace-nowrap transition-all cursor-pointer flex items-center gap-1.5 ${
                activeTab === 'final-scores'
                  ? 'bg-zinc-900 text-white dark:bg-zinc-800 dark:text-[#3B82F6] shadow-xs'
                  : 'text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/50'
              }`}
            >
              <Award className="w-3.5 h-3.5" />
              <span>Final Scores (Semester Summary)</span>
            </button>

            {activeAssignments.map((asg, idx) => {
              const isSelected = activeTab === asg.id || activeTab === asg.shortCode.toLowerCase();
              return (
                <button
                  key={asg.id}
                  onClick={() => setActiveTab(asg.id)}
                  className={`px-3 py-2 text-xs font-medium rounded-xl whitespace-nowrap transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-zinc-900 text-white dark:bg-zinc-800 dark:text-[#3B82F6] shadow-xs'
                      : 'text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/50'
                  }`}
                >
                  {asg.shortCode}
                </button>
              );
            })}
          </div>

          {/* Action Buttons */}
          <div className="flex flex-wrap items-center gap-2 self-start lg:self-auto">
            {/* Search Input */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-zinc-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                placeholder="Search student or ID..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="pl-8 pr-3 py-1.5 text-xs rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 text-zinc-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-[#3B82F6] w-44"
              />
            </div>

            <button
              onClick={handleCopyTable}
              className="px-3 py-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 rounded-xl transition-colors cursor-pointer flex items-center gap-1.5"
              title="Copy table to clipboard as TSV for pasting directly into Excel"
            >
              {copiedNotification ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copiedNotification ? 'Copied!' : 'Copy TSV'}</span>
            </button>

            <button
              onClick={handleExportCSV}
              className="px-3 py-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 rounded-xl transition-colors cursor-pointer flex items-center gap-1.5"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Export CSV</span>
            </button>

            <button
              onClick={handleExportAll10Tables}
              className="px-3 py-1.5 text-xs font-semibold text-white bg-[#3B82F6] hover:bg-[#2563EB] rounded-xl transition-colors cursor-pointer flex items-center gap-1.5 shadow-xs"
              title="Export all 10 tables together in one combined CSV"
            >
              <FileSpreadsheet className="w-3.5 h-3.5" />
              <span>Export 10-Table Bundle</span>
            </button>
          </div>
        </div>

        {/* Table Content Area */}
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-2xl overflow-hidden shadow-xs">
          <div className="overflow-x-auto max-h-[620px] relative">
            {activeTab === 'final-scores' ? (
              /* TAB 10: Final Semester Summary Matrix */
              <table className="w-full text-left text-xs border-collapse">
                <thead className="bg-zinc-900 text-white sticky top-0 z-20 font-medium">
                  <tr>
                    <th className="py-2.5 px-3 border-b border-r border-zinc-800 sticky left-0 z-30 bg-zinc-900 w-10 text-center">
                      #
                    </th>
                    <th className="py-2.5 px-3 border-b border-r border-zinc-800 sticky left-10 z-30 bg-zinc-900 min-w-[140px]">
                      Student Name
                    </th>
                    <th className="py-2.5 px-3 border-b border-r border-zinc-800 sticky left-[180px] z-30 bg-zinc-900 min-w-[100px] font-mono">
                      Student ID
                    </th>
                    {activeAssignments.map(asg => (
                      <th
                        key={asg.id}
                        className="py-2.5 px-3 text-center border-b border-r border-zinc-800 font-mono min-w-[70px]"
                        title={`${asg.title} (Scale: ${asg.targetScale ?? 1.0})`}
                      >
                        <div>{asg.shortCode}</div>
                        <div className="text-[10px] text-zinc-400 font-normal">/{asg.targetScale ?? 1.0}</div>
                      </th>
                    ))}
                    <th className="py-2.5 px-4 text-center border-b border-zinc-800 bg-[#3B82F6]/20 text-[#3B82F6] font-mono font-bold min-w-[130px] sticky right-0 z-20">
                      <div>Final Total</div>
                      <div className="text-[10px] text-zinc-400 font-normal">/{semesterScale}.00 Target</div>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/80 bg-white dark:bg-[#09090B]">
                  {filteredStudents.length === 0 ? (
                    <tr>
                      <td colSpan={activeAssignments.length + 4} className="py-12 text-center text-zinc-400">
                        No students found matching your search.
                      </td>
                    </tr>
                  ) : (
                    filteredStudents.map((s, idx) => {
                      return (
                        <tr
                          key={s.studentId}
                          className="hover:bg-zinc-50 dark:hover:bg-zinc-800/40 transition-colors group"
                        >
                          {/* Row # */}
                          <td className="py-2 px-3 text-center text-zinc-400 border-r border-zinc-100 dark:border-zinc-800 sticky left-0 z-10 bg-white dark:bg-[#09090B] group-hover:bg-zinc-50 dark:group-hover:bg-zinc-800/60 font-mono text-[11px]">
                            {idx + 1}
                          </td>

                          {/* Student Name */}
                          <td className="py-2 px-3 font-medium text-zinc-900 dark:text-zinc-100 border-r border-zinc-100 dark:border-zinc-800 sticky left-10 z-10 bg-white dark:bg-[#09090B] group-hover:bg-zinc-50 dark:group-hover:bg-zinc-800/60 whitespace-nowrap">
                            {s.studentName}
                          </td>

                          {/* Student ID */}
                          <td className="py-2 px-3 font-mono text-zinc-600 dark:text-zinc-400 border-r border-zinc-100 dark:border-zinc-800 sticky left-[180px] z-10 bg-white dark:bg-[#09090B] group-hover:bg-zinc-50 dark:group-hover:bg-zinc-800/60 whitespace-nowrap text-[11px]">
                            {s.studentNumber}
                          </td>

                          {/* Assignment Scores (HW1..HW5, Q1..Q4) */}
                          {activeAssignments.map(asg => {
                            const asgScore = s.assignmentScores[asg.id];
                            const display = asgScore ? asgScore.displayScore.toFixed(2) : '—';
                            const isOverflow = asgScore?.isOverflow;

                            return (
                              <td
                                key={asg.id}
                                className="py-2 px-3 text-center font-mono border-r border-zinc-100 dark:border-zinc-800 text-zinc-800 dark:text-zinc-200"
                              >
                                <span className={isOverflow ? 'text-emerald-600 dark:text-emerald-400 font-bold' : ''}>
                                  {display}
                                </span>
                              </td>
                            );
                          })}

                          {/* Final Semester Score */}
                          <td className="py-2 px-4 text-center font-mono font-black border-l border-zinc-200 dark:border-zinc-800 bg-[#3B82F6]/5 text-[#3B82F6] sticky right-0 z-10 group-hover:bg-[#3B82F6]/10">
                            <div className="flex items-center justify-center gap-1.5">
                              <span className="text-sm">{s.displaySemesterTotal.toFixed(2)}</span>
                              {s.isSemesterOverflow && (
                                <span
                                  className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 font-bold"
                                  title={`Bonus overflow: Exceeds ${semesterScale}.00 standard maximum!`}
                                >
                                  +Bonus
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}

                  {/* Summary Row (Averages) */}
                  <tr className="bg-zinc-100 dark:bg-zinc-900 font-semibold text-zinc-900 dark:text-zinc-100 border-t-2 border-zinc-300 dark:border-zinc-700 sticky bottom-0 z-20">
                    <td className="py-2.5 px-3 text-center border-r border-zinc-200 dark:border-zinc-800 sticky left-0 z-30 bg-zinc-100 dark:bg-zinc-900">
                      —
                    </td>
                    <td className="py-2.5 px-3 border-r border-zinc-200 dark:border-zinc-800 sticky left-10 z-30 bg-zinc-100 dark:bg-zinc-900">
                      Class Mean
                    </td>
                    <td className="py-2.5 px-3 border-r border-zinc-200 dark:border-zinc-800 sticky left-[180px] z-30 bg-zinc-100 dark:bg-zinc-900 text-zinc-400 font-mono text-[11px]">
                      {filteredStudents.length} Students
                    </td>
                    {activeAssignments.map(asg => (
                      <td
                        key={asg.id}
                        className="py-2.5 px-3 text-center font-mono text-zinc-900 dark:text-zinc-100 border-r border-zinc-200 dark:border-zinc-800"
                      >
                        {finalScoreAverages.asgAverages[asg.id] ?? '—'}
                      </td>
                    ))}
                    <td className="py-2.5 px-4 text-center font-mono font-black text-[#3B82F6] bg-[#3B82F6]/15 border-l border-zinc-300 dark:border-zinc-700 sticky right-0 z-20">
                      {finalScoreAverages.overallAverage}
                    </td>
                  </tr>
                </tbody>
              </table>
            ) : currentAssignment ? (
              /* TAB 1..9: Individual Assessment Detailed Question Breakdown */
              <table className="w-full text-left text-xs border-collapse">
                <thead className="bg-zinc-900 text-white sticky top-0 z-20 font-medium">
                  <tr>
                    <th className="py-2.5 px-3 border-b border-r border-zinc-800 sticky left-0 z-30 bg-zinc-900 w-10 text-center">
                      #
                    </th>
                    <th className="py-2.5 px-3 border-b border-r border-zinc-800 sticky left-10 z-30 bg-zinc-900 min-w-[140px]">
                      Student Name
                    </th>
                    <th className="py-2.5 px-3 border-b border-r border-zinc-800 sticky left-[180px] z-30 bg-zinc-900 min-w-[100px] font-mono">
                      Student ID
                    </th>
                    {currentAssignment.questions.map(q => (
                      <th
                        key={q.id}
                        className={`py-2.5 px-3 text-center border-b border-r border-zinc-800 font-mono min-w-[65px] ${
                          q.isBonus ? 'bg-amber-500/10 text-amber-400' : ''
                        }`}
                      >
                        <div className="truncate max-w-[90px]">{q.title}</div>
                        <div className="text-[10px] text-zinc-400 font-normal">
                          /{q.directMaxScore}p {q.isBonus && <span className="font-bold text-amber-400">(Bonus)</span>}
                        </div>
                      </th>
                    ))}
                    <th className="py-2.5 px-3 text-center border-b border-r border-zinc-800 font-mono font-bold min-w-[80px]">
                      <div>Sum Points</div>
                      <div className="text-[10px] text-zinc-400 font-normal">مجموع نمره</div>
                    </th>
                    <th className="py-2.5 px-3 text-center border-b border-zinc-800 bg-[#3B82F6]/20 text-[#3B82F6] font-mono font-bold min-w-[100px] sticky right-0 z-20">
                      <div>Score /{currentAssignment.targetScale ?? 1.0}</div>
                      <div className="text-[10px] text-zinc-400 font-normal">نمره نهایی (Auto Denom: {calculateAssignmentDenominator(currentAssignment)})</div>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/80 bg-white dark:bg-[#09090B]">
                  {filteredStudents.length === 0 ? (
                    <tr>
                      <td colSpan={currentAssignment.questions.length + 5} className="py-12 text-center text-zinc-400">
                        No students found.
                      </td>
                    </tr>
                  ) : (
                    filteredStudents.map((s, idx) => {
                      const grade = activeGrades[`${s.studentId}_${currentAssignment.id}`];
                      const calc = calculateStudentAssignmentScore(currentAssignment, grade?.scores);

                      return (
                        <tr
                          key={s.studentId}
                          className="hover:bg-zinc-50 dark:hover:bg-zinc-800/40 transition-colors group"
                        >
                          <td className="py-2 px-3 text-center text-zinc-400 border-r border-zinc-100 dark:border-zinc-800 sticky left-0 z-10 bg-white dark:bg-[#09090B] group-hover:bg-zinc-50 dark:group-hover:bg-zinc-800/60 font-mono text-[11px]">
                            {idx + 1}
                          </td>
                          <td className="py-2 px-3 font-medium text-zinc-900 dark:text-zinc-100 border-r border-zinc-100 dark:border-zinc-800 sticky left-10 z-10 bg-white dark:bg-[#09090B] group-hover:bg-zinc-50 dark:group-hover:bg-zinc-800/60 whitespace-nowrap">
                            {s.studentName}
                          </td>
                          <td className="py-2 px-3 font-mono text-zinc-600 dark:text-zinc-400 border-r border-zinc-100 dark:border-zinc-800 sticky left-[180px] z-10 bg-white dark:bg-[#09090B] group-hover:bg-zinc-50 dark:group-hover:bg-zinc-800/60 whitespace-nowrap text-[11px]">
                            {s.studentNumber}
                          </td>
                          {currentAssignment.questions.map(q => {
                            const val = grade?.scores?.[q.id];
                            return (
                              <td
                                key={q.id}
                                className={`py-2 px-3 text-center font-mono border-r border-zinc-100 dark:border-zinc-800 ${
                                  q.isBonus ? 'bg-amber-500/5 text-amber-600 dark:text-amber-400 font-semibold' : 'text-zinc-700 dark:text-zinc-300'
                                }`}
                              >
                                {val !== undefined && val !== null ? val : '—'}
                              </td>
                            );
                          })}
                          <td className="py-2 px-3 text-center font-mono font-bold text-zinc-900 dark:text-zinc-100 border-r border-zinc-100 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-900/30">
                            {calc.totalEarned.toFixed(1)}
                          </td>
                          <td className="py-2 px-3 text-center font-mono font-bold border-l border-zinc-200 dark:border-zinc-800 bg-[#3B82F6]/5 text-[#3B82F6] sticky right-0 z-10 group-hover:bg-[#3B82F6]/10">
                            <div className="flex items-center justify-center gap-1">
                              <span>{calc.displayScore.toFixed(2)}</span>
                              {calc.isOverflow && (
                                <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 font-bold">
                                  +Bonus
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
};
