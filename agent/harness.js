const { getRateCard } = require('../tools/rate_card');
const { checkStock } = require('../tools/stock');
const { SYSTEM_PROMPT } = require('./prompt');

/**
 * Mock TrueForge Harness
 * This simulates the agent loop required by the hackathon criteria.
 */
class TrueForgeHarness {
    constructor() {
        this.state = 'Received';
        this.memory = [];
    }

    log(message) {
        console.log(`[Agent: ${this.state}] ${message}`);
    }

    async runExtraction(enquiryText) {
        this.log('Extracting specifications...');
        // Simulate LLM extraction based on enquiry
        let extractedSpecs = {
            customer: "Unknown",
            items: [],
            missing: []
        };
        
        // Simple mock extraction based on the demo scenarios
        if (enquiryText.toLowerCase().includes('missing thickness') || (enquiryText.toLowerCase().includes('200x100 ') && !enquiryText.toLowerCase().includes('x8'))) {
           extractedSpecs.missing.push("thickness_mm");
        } else {
            extractedSpecs.items.push({
                name: "bracket",
                material: "MS",
                length_mm: 200, width_mm: 100, thickness_mm: 8,
                qty: 50,
                ops: { cutting: 1, bends: 1, weld_m: 0, holes: 2 },
                finish: "powder_coat"
            });
        }
        return extractedSpecs;
    }

    async processEnquiry(enquiryText) {
        this.state = 'Received';
        this.log(`New enquiry: "${enquiryText}"`);
        
        // 1. Extraction Phase
        this.state = 'Extracted';
        const specs = await this.runExtraction(enquiryText);
        
        if (specs.missing.length > 0) {
            this.state = 'NeedsClarification';
            this.log(`Stopping. Missing specs: ${specs.missing.join(', ')}`);
            return { status: 'NeedsClarification', question: `Could you please provide the ${specs.missing[0]}?` };
        }

        // 2. Costing Phase
        this.log('Calling get_rate_card...');
        const rates = await getRateCard();
        
        this.log(`Calling check_stock for ${specs.items[0].material}...`);
        const stock = await checkStock(specs.items[0].material, specs.items[0].thickness_mm, 100);

        this.state = 'Costed';
        this.log('Costing complete via sandbox (mocked).');
        
        // 3. Margin Approval Phase
        let margin = 0.20;
        let targetPriceMatch = enquiryText.match(/Rs (\d+) total/);
        if (targetPriceMatch) {
            margin = 0.09; // Simulate margin dropping below floor
        }
        
        if (margin < 0.12) {
            this.state = 'MarginApproval';
            this.log('Margin is below 12% floor. Pausing for approval...');
            return { status: 'MarginApproval', alert: 'Margin too low.' };
        }

        // 4. PDF Ready Phase
        this.state = 'PdfReady';
        this.log('Generated quote PDF (mocked).');

        // 5. Send Approval Phase
        this.state = 'SendApproval';
        this.log('Waiting for owner to approve sending the quote...');
        return { status: 'SendApproval', alert: 'Quote ready to send.' };
    }
}

module.exports = { TrueForgeHarness };
