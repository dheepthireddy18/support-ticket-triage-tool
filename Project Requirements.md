# Project Requirements

# **Team 1: Support Ticket Triage Tool: Members: Rachit Shah, Natalie King, Sanket Deshmukh, Dheepthi Reddy**

### **The Customer problem**

A support team gets hundreds of tickets a day. Urgent ones get buried, and managers start each morning without knowing what needs attention first.

### **What the team delivers**

A command line tool that:

* Reads a file of tickets.
* Rejects bad records.
* Assigns each ticket a priority.
* Prints a daily summary report for the support manager.

### **Project agreement (Monday)**

* Define the ticket shape: fields (ID, subject, body, customer, created time) and what a clean ticket looks like.
* Document the shape in the team's README. Changes require team approval.

### **Optional layer**

If API keys are approved, an LLM can classify a ticket's urgency and topic. The tool must still work with plain rules when no key is present.

## **Rachit Shah: Data Intake and Validation**

### **Builds**

* Read tickets from a CSV or JSON file.
* Reject or flag bad records (missing fields, bad dates, duplicates) with a clear reason for each.
* Provide a `Ticket` class for other components to use.
* Write pytest tests for this piece, including bad record cases.

### **Presents on Friday**

* Show examples of bad data and how the tool handles each case.
* Explain the chosen ticket shape.

### **Be ready to answer**

* What happens to a rejected ticket? Does the customer know?
* How would you handle an unexpected new column in the input file?

## **Natalie King: Priority Rules**

### **Builds**

* Create a base class for priority rules, with at least three subclasses (e.g., keyword rules, customer tier rules, waiting time rules).
* Combine rule outputs into a single priority score for each ticket.
* Write pytest tests for the rules, including cases where two rules disagree.
* Optional: add an LLM-based urgency classifier rule.

### **Presents on Friday**

* Demonstrate how a ticket gets its priority with two or three real examples.
* Explain why inheritance works here and how to add a new rule.

### **Be ready to answer**

* What happens if two rules disagree?
* How could a support manager change a rule without editing code?

## **Sanket Suni: Daily Summary Report**

### **Builds**

* Take prioritized tickets and generate a readable report: counts by priority, top urgent tickets, tickets waiting longest.
* Save the report to a file.
* Write pytest tests for the report, including a day with no urgent tickets.

### **Presents on Friday**

* Show how the report is built from prioritized tickets with real output.
* Explain the report layout from the manager's perspective.

### **Be ready to answer**

* What does the manager do first after reading this?
* What would you add if they wanted the report by email?

## **Dheepthi Reddy: Command Line, CI, README, and Integration Test**

### **Builds**

* Create the command line entry point that runs the entire tool.
* Write one integration test that runs the full tool on the sample ticket file and checks the report.
* Set up the CI workflow so tests run on every pull request.
* Prepare a README with instructions on running the tool and the ticket shape.
* Keep the team's task board (Linear or Microsoft Planner) up to date.

### **Presents on Friday**

* Live demo of the full tool, from input file to report.
* Explain what the integration test covers and what it doesn’t.
* Walk through the task board and pull request history to show the team’s workflow.

### **Be ready to answer**

* What was the hardest problem when integrating the pieces?
* How do you ensure the pieces still work together after changes?

## **Rules for the Whole Team**

* Everyone commits on feature branches and merges by pull request. Each commit must be attributed to its author.
* Each person writes tests for their piece and presents it, even if AI helped write it.
* Assign tasks in Linear or Microsoft Planner. Paste pull request links into the corresponding task.