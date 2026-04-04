# Custom Course Creation Wizard - Implementation Specification

## Overview
Comprehensive multi-step wizard for node-by-node custom course creation with LLM-assisted structure generation and content hydration.

## User Flow

### Step 1: Course Metadata
**Screen**: Course Information
- **Inputs**:
  - Course Title (required)
  - Course Description (optional, textarea)
  - Teaching Style (dropdown: Default, ELI5, Academic, Analogies, Drill)
- **Actions**:
  - "Next" → Proceed to Step 2
  - "Cancel" → Close modal

### Step 2: Module Builder
**Screen**: Build Modules
- **Module List** (initially empty):
  - Each module shows: Title, Depth badge, Edit/Delete buttons
  - Drag handles for reordering
- **Add Module Button** opens Module Editor:
  - Module Title (required)
  - Module Context/Description (textarea, helps LLM generate better content)
  - Depth Level (slider 1-5 with descriptions)
  - Source File Upload (optional: .txt, .md, .pdf, .epub)
  - Save/Cancel buttons
- **Actions**:
  - "Add Module" → Open module editor
  - "Edit" → Reopen module editor with existing data
  - "Delete" → Remove module from list
  - "Back" → Return to Step 1
  - "Preview Structure" → Proceed to Step 3 (requires at least 1 module)

### Step 3: Structure Preview
**Screen**: Generated Course Structure
- **Loading State**: "Generating course structure..."
- **Tree Visualization**:
  - Course
    - Module 1
      - Unit 1
        - Lesson 1
          - Concept 1
          - Concept 2
        - Lesson 2
      - Unit 2
    - Module 2
- **Expandable/Collapsible** nodes
- **Counts**: Show number of units, lessons, concepts per module
- **Actions**:
  - "Back to Modules" → Return to Step 2 for editing
  - "Finalize & Hydrate" → Proceed to Step 4

### Step 4: Hydration & Commit
**Screen**: Creating Course (reuse existing creation modal)
- **Progress Indicators**:
  - "Preparing database..."
  - "Hydrating Module 1/N: [Module Title]"
  - "Vectorizing content..."
  - "Finalizing course..."
- **Progress Bar**: 0% → 100%
- **Activity Log**: Timestamped entries
- **On Success**: Redirect to /courses

## Technical Implementation

### Frontend (courses.html)

#### Wizard State Management
```javascript
const customCourseWizard = {
    currentStep: 1,
    courseData: {
        title: '',
        description: '',
        teaching_style: ''
    },
    modules: [],
    generatedStructure: null
};
```

#### Step Navigation
```javascript
function showWizardStep(step) {
    // Hide all steps
    document.querySelectorAll('.wizard-step').forEach(el => el.classList.add('hidden'));
    // Show current step
    document.getElementById(`wizard-step-${step}`).classList.remove('hidden');
    // Update progress indicator
    updateWizardProgress(step);
}
```

#### Module Editor
```javascript
function openModuleEditor(moduleIndex = null) {
    // If moduleIndex is null, create new module
    // Otherwise, edit existing module
    // Show modal with form
    // On save, update customCourseWizard.modules
}
```

### Backend Endpoints

#### 1. Preview Structure Generation
**Endpoint**: `POST /api/custom_course/preview`
**Request**:
```json
{
    "title": "Advanced Rocket Science",
    "teaching_style": "Academic",
    "modules": [
        {
            "title": "Propulsion Systems",
            "context": "Focus on chemical and ion propulsion",
            "depth": 4
        }
    ]
}
```
**Response**:
```json
{
    "structure": {
        "course_uid": "temp_preview_123",
        "modules": [
            {
                "title": "Propulsion Systems",
                "units": [
                    {
                        "title": "Chemical Propulsion",
                        "lessons": [
                            {
                                "title": "Rocket Equation",
                                "concepts": ["Tsiolkovsky Equation", "Specific Impulse"]
                            }
                        ]
                    }
                ]
            }
        ]
    }
}
```

**Implementation**:
- Use SkeletonBuilder to generate structure
- Call LLM with module context to generate units/lessons/concepts
- Return JSON structure (don't commit to DB yet)

#### 2. Final Creation & Hydration
**Endpoint**: `POST /api/custom_course/create`
**Request**:
```json
{
    "title": "Advanced Rocket Science",
    "teaching_style": "Academic",
    "modules": [...],
    "structure": {...}  // From preview
}
```
**Response**:
```json
{
    "status": "ok",
    "course_uid": "course_abc123",
    "message": "Course created successfully"
}
```

**Implementation**:
- Create course and module nodes in DB
- Use provided structure to create units/lessons/concepts
- Run ContentHydrator for each module
- Emit status updates via `/api/update_thinking_status`
- Return success/failure

### Integration Points

#### Reuse Existing Components
1. **Creation Progress Modal**: Already exists, reuse for Step 4
2. **SkeletonBuilder**: Use for LLM-based structure generation
3. **ContentHydrator**: Use for content filling
4. **LocalFileProvider**: Use for source file handling
5. **Status Updates**: Use existing `/api/update_thinking_status` mechanism

#### Styling
- Match existing modal styles
- Use existing color variables
- Maintain consistent button styles
- Use existing form input styles

## File Changes Required

### 1. services/web-ui/templates/courses.html
- Replace simple custom course modal with multi-step wizard
- Add wizard state management JavaScript
- Add module editor component
- Add structure preview tree component
- Wire up step navigation

### 2. services/web-ui/app.py
- Add `/api/custom_course/preview` proxy endpoint

### 3. services/rag/librarian.py
- Implement `/api/custom_course/preview` endpoint
- Modify `/api/custom_course/create` to use preview structure
- Add LLM calls for structure generation

### 4. services/core/course_builder.py (if needed)
- Add helper methods for preview generation
- Ensure SkeletonBuilder can work with module context

## Success Criteria
1. User can create course metadata in Step 1
2. User can add/edit/delete/reorder modules in Step 2
3. LLM generates structure preview in Step 3
4. Content hydration completes successfully in Step 4
5. Course appears in courses list after creation
6. All existing tests still pass
7. UI matches existing design patterns

## Testing Plan
1. Unit tests for new endpoints
2. Integration test for full wizard flow
3. Manual testing of UI interactions
4. Verify LLM structure generation quality
5. Verify content hydration with source files
6. Test error handling at each step

## Rollout Strategy
1. Implement backend endpoints first
2. Implement frontend wizard UI
3. Test preview generation
4. Test full creation flow
5. Add error handling and validation
6. Polish UI and add loading states
7. Document new feature
